from __future__ import print_function
from django.http import JsonResponse
from collections import defaultdict
from django.db.models import Min, Max, Q, F, Count, OuterRef, Subquery
from django.db.models.functions import Cast
from django.forms.models import model_to_dict
from pprint import pprint
from operator import itemgetter
import sys
import time
import math
import numpy as np
import traceback
from timeit import default_timer as now

from query.base_models import ModelDelegator

def fprint(*args):
    print(*args)
    sys.stdout.flush()


def load_stdlib_models(dataset):
    m = ModelDelegator(dataset)
    m.import_all(globals())


def at_fps(qs, n=1):
    return qs.annotate(_tmp=F('number') % (Cast('video__fps', models.IntegerField()) / n)).filter(
        _tmp=0)


def bbox_to_dict(f):
    return {
        'id': f.id,
        'type': 'bbox',
        'bbox_x1': f.bbox_x1,
        'bbox_x2': f.bbox_x2,
        'bbox_y1': f.bbox_y1,
        'bbox_y2': f.bbox_y2,
        'bbox_score': f.bbox_score,
        'labeler': f.labeler.id
    }


def pose_to_dict(f):
    return {
        'id': f.id,
        'labeler': f.labeler.id,
        'type': 'pose',
        'keypoints': {
            'hand_left': f.hand_keypoints()[0].tolist(),
            'hand_right': f.hand_keypoints()[1].tolist(),
            'pose': f.pose_keypoints().tolist(),
            'face': f.face_keypoints().tolist()
        }
    }


def bbox_area(f):
    return (f.bbox_x2 - f.bbox_x1) * (f.bbox_y2 - f.bbox_y1)


def bbox_midpoint(f):
    return np.array([(f.bbox_x1 + f.bbox_x2) / 2, (f.bbox_y1 + f.bbox_y2) / 2])


def bbox_dist(f1, f2):
    return np.linalg.norm(bbox_midpoint(f1) - bbox_midpoint(f2))


def group_result(materialized_result):
    grouped_result = defaultdict(list)
    for r in materialized_result:
        grouped_result[(r['video'], r['start_frame'])].extend(r['objects'])

    flat_result = [{
        'video': t1,
        'start_frame': t2,
        'objects': r
    } for (t1, t2), r in grouped_result.iteritems()]
    return sorted(flat_result, key=itemgetter('video', 'start_frame'))


def simple_result(result, ty):
    return {
        'result': [{
            'type': 'flat',
            'elements': [r]
        } for r in result],
        'count': len(result),
        'type': ty
    }


def filter_poses(ty, fn, used_kps, poses=None):
    filtered = []
    if poses is None:
        poses = Pose.objects.all().order_by('id').select_related('frame',
                                                                 'frame__video')[:100000:10]
    for pose in poses:
        kps = getattr(pose, '{}_keypoints'.format(ty))()
        bad = False
        for k in used_kps:
            if kps[k][2] == 0:
                bad = True
                break
        if bad:
            continue

        if fn(kps):
            filtered.append(pose)
    return filtered


# TODO(wcrichto): allow pagination to make repeated requests to backend
LIMIT = 500
STRIDE = 1


def qs_to_result(result, group=False, segment=False, stride=1, shuffle=False):
    try:
        sample = result[0]
    except IndexError:
        return {'result': [], 'count': 0, 'type': ''}

    count = result.count()

    if shuffle:
        result = result.order_by('?')

    materialized_result = []
    cls_name = '_'.join(sample.__class__.__name__.split('_')[1:])
    if cls_name == 'Frame':
        for frame in result[:LIMIT * stride:stride]:
            materialized_result.append({
                'video': frame.video.id,
                'start_frame': frame.id,
                'objects': []
            })

    elif cls_name == 'Face' or cls_name == 'Pose':
        for inst in result[:LIMIT * stride:stride]:
            r = {
                'video': inst.frame.video.id,
                'start_frame': inst.frame.id,
            }
            if cls_name == 'Face':
                r['objects'] = [bbox_to_dict(inst)]
            elif cls_name == 'Pose':
                r['objects'] = [pose_to_dict(inst)]
            materialized_result.append(r)

    elif cls_name == 'FaceTrack':
        # tracks = list(result[:LIMIT*stride:stride])

        # TODO: move to django 1.11, enable subquery

        # face_ids = [f.id for f in faces]
        # sq = Face.objects.filter(face_id=OuterRef('pk')).annotate(min_frame=Min('frame__number'))
        # tracks = Face \
        #     .filter(id__in=face_ids) \
        #     .annotate(min_frame=Subquery(sq.values('min_frame'))) \
        #     .values()

        for t in result.values('id')[:1000]:
            bounds = Face.objects.filter(track=t['id']).aggregate(
                min_frame=Min('frame__number'), max_frame=Max('frame__number'))
            assert (bounds['min_frame'] is not None)
            if bounds['min_frame'] == bounds['max_frame']:
                continue

            min_face = Face.objects.filter(frame__number=bounds['min_frame'], track=t['id'])[0]
            video = min_face.frame.video.id
            materialized_result.append({
                'video':
                video,
                'track':
                t['id'],
                'start_frame':
                Frame.objects.get(video_id=video, number=bounds['min_frame']).id,
                'end_frame':
                Frame.objects.get(video_id=video, number=bounds['max_frame']).id,
                'objects': [bbox_to_dict(min_face)]
            })

            if len(materialized_result) == LIMIT:
                break

        materialized_result.sort(key=itemgetter('video', 'start_frame'))

    ty_name = cls_name

    if group:
        materialized_result = group_result(materialized_result)
        ty_name = 'Frame grouped by {}'.format(ty_name)

    if segment:
        tracks = [r['track'] for r in materialized_result]
        intervals = [(r['video'], r['start_frame'], r['end_frame']) for r in materialized_result]
        points = []
        for (video, start, end) in intervals:
            start_num = Frame.objects.get(id=start).number
            end_num = Frame.objects.get(id=end).number
            points.extend([(video, start_num, start, False), (video, end_num, end, True)])
        points.sort(key=itemgetter(0, 1, 3))

        pprint(points)
        sys.stdout.flush()

        intervals_active = 0
        boundaries = []
        i = 0
        intvl_id = 0
        while i < len(points):
            num_intervals = 1
            (_, _, frame, is_end) = points[i]
            while i + 1 < len(points):
                if points[i + 1][2] == frame:
                    num_intervals += 1
                    i += 1
                else:
                    break

            pprint((points[i], num_intervals))
            sys.stdout.flush()

            if not is_end:
                intervals_active += num_intervals
                boundaries.append((frame, points[i + 1][2], intvl_id))
            else:
                intervals_active -= num_intervals
                if intervals_active > 0:
                    boundaries.append((frame, points[i + 1][2], intvl_id))
                else:
                    intvl_id += 1

            i += 1

        groups = []
        materialized_result = []
        cur_intvl_id = boundaries[0][2]
        for (start, end, intvl_id) in boundaries:
            if intvl_id != cur_intvl_id:
                video = Video.objects.get(id=materialized_result[0]['video'])
                intvl_start = materialized_result[0]['start_frame']
                intvl_end = materialized_result[-1]['end_frame']
                format_time = lambda t: time.strftime('%H:%M:%S', time.gmtime(math.floor(t / video.fps)))

                groups.append({
                    'type':
                    'contiguous',
                    'label':
                    '{} -- {}'.format(
                        format_time(Frame.objects.get(id=intvl_start).number),
                        format_time(Frame.objects.get(id=intvl_end).number)),
                    'elements':
                    materialized_result
                })
                materialized_result = []
                cur_intvl_id = intvl_id

            f = Frame.objects.filter(id=start).select_related('video').get()
            materialized_result.append({
                'video':
                f.video.id,
                'start_frame':
                start,
                'end_frame':
                end,
                #'objects': [bbox_to_dict(face) for face in Face.objects.filter(frame=f)]
                'objects':
                [bbox_to_dict(face) for face in Face.objects.filter(frame=f, track__in=tracks)]
            })

        ty_name = '{} (segmented)'.format(ty_name)
    else:
        groups = [{'type': 'flat', 'label': '', 'elements': [r]} for r in materialized_result]

    return {'result': groups, 'count': count, 'type': ty_name}