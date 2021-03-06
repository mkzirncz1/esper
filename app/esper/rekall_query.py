# import query sets
from query.models import Video, Face, FaceIdentity, Commercial
from django.db.models import F, Q

# import esper utils
from esper.prelude import *

# import rekall
from esper.rekall import *
from rekall.interval_list import Interval, IntervalList
from rekall.video_interval_collection import VideoIntervalCollection
from rekall.temporal_predicates import *
from rekall.spatial_predicates import *
from rekall.logical_predicates import *
from rekall.parsers import in_array, bbox_payload_parser
from rekall.merge_ops import payload_plus
from rekall.payload_predicates import payload_satisfies
from rekall.list_predicates import length_exactly

# import caption search
from esper.captions import *


# ============== Basic help functions ============== 

def count_intervals(intrvlcol):
    num_intrvl = 0
    for intrvllist in intrvlcol.get_allintervals().values():
        num_intrvl += intrvllist.size()
    return num_intrvl


def count_duration(intrvlcol):
    if type(intrvlcol) == IntervalList:
        intrvllist = intrvlcol
        if intrvllist.size() > 0:
            duration = sum([i.end - i.start for i in intrvllist.get_intervals()])
        else:
            duration = 0
    else:
        if count_intervals(intrvlcol) > 0:
            duration = sum([i.end - i.start for _, intrvllist in intrvlcol.get_allintervals().items() \
                            for i in intrvllist.get_intervals() ])
        else:
            duration = 0
    return duration


def intrvlcol2list(intrvlcol, with_duration=True):
    interval_list = []
    for video_id, intrvllist in intrvlcol.get_allintervals().items():
        if with_duration:
            video = Video.objects.filter(id=video_id)[0]
        for i in intrvllist.get_intervals():
            if i.start > video.num_frames:
                continue
            if with_duration:
                interval_list.append((video_id, i.start, i.end, (i.end - i.start) / video.fps))
            else:
                interval_list.append((video_id, i.start, i.end))
    print("Get {} intervals from interval collection".format(len(interval_list)))
    return interval_list


def intrvlcol2result(intrvlcol, flat=False):
    if not flat:
        return intrvllists_to_result(intrvlcol.get_allintervals())
    else:
        return interval2result(intrvlcol2list(intrvlcol))
    

def interval2result(intervals):
    materialized_result = [
        {'video': video_id,
#             'track': t.id,
         'min_frame': sfid,
         'max_frame': efid }
        for video_id, sfid, efid, duration in intervals ]
    count = len(intervals)
    groups = [{'type': 'flat', 'label': '', 'elements': [r]} for r in materialized_result]
    return {'result': groups, 'count': count, 'type': 'Video'}


def intrvlcol_frame2second(intrvlcol):
    intrvllists_second = {}
    for video_id, intrvllist in intrvlcol.get_allintervals().items():
        video = Video.objects.filter(id=video_id)[0]
        fps = video.fps
        intrvllists_second[video_id] = IntervalList([(i.start / fps, i.end / fps, i.payload) \
                                                  for i in intrvllist.get_intervals()] )
    return VideoIntervalCollection(intrvllists_second)


def intrvlcol_second2frame(intrvlcol):
    intrvllists_frame = {}
    for video_id, intrvllist in intrvlcol.get_allintervals().items():
        video = Video.objects.filter(id=video_id)[0]
        fps = video.fps
        intrvllists_frame[video_id] = IntervalList([(int(i.start * fps), int(i.end * fps), i.payload) \
                                                  for i in intrvllist.get_intervals()] )
    return VideoIntervalCollection(intrvllists_frame)


def intrvllist2list(intrvllist):
    intervals = []
    for i in intrvllist.get_intervals():
        intervals.append((i.start, i.end, i.payload))
    return intervals


def split_intrvlcol(intrvlcol, seg_length):
    intrvllists_split = {}
    for video_id, intrvllist in intrvlcol.get_allintervals().items():
        intervals_split = []
        for i in intrvllist.get_intervals():
            duration = i.end - i.start
            start = i.start
            while duration > 0:
                if duration > seg_length:
                    intervals_split.append((start, start + seg_length, i.payload))
                    duration -= seg_length
                    start += seg_length
                else:
                    intervals_split.append((start, start + duration, i.payload))
                    duration = 0
        intrvllists_split[video_id] = IntervalList(intervals_split)
    return VideoIntervalCollection(intrvllists_split)


def remove_isolated_interval(intrvlcol, min_duration=10, max_isolation=60):
    intrvlcol_filtered = intrvlcol \
        .filter_length(max_length = min_duration) \
        .filter_against(intrvlcol,
            predicate=or_pred(before(max_dist=max_isolation),
            after(max_dist=max_isolation), arity=2),
            working_window=max_isolation) \
        .set_union(intrvlcol \
            .filter_length(min_length = min_duration))
            
    return intrvlcol_filtered


# ============== Queries with rekall ==============   

def get_commercial_intrvlcol(video_ids=None, granularity='frame'):
    if video_ids is None:
        commercial_qs = Commercial.objects.all()
    else:
        commercial_qs = Commercial.objects.filter(video_id__in=video_ids)
        
    commercial_intrvllists = qs_to_intrvllists(
            commercial_qs.annotate(video_id=F("video_id")))
    commercial = VideoIntervalCollection(commercial_intrvllists)
    if granularity == 'second':
        commercial = intrvlcol_frame2second(commercial)
    return commercial
        

def get_person_intrvlcol(person_list=None, video_ids=None, 
                         probability=0.9, face_size=None, stride_face=False, labeler='new',
                         exclude_person=False, granularity='frame', payload_type='shot_id'):
    
    def identity_filter(person_list):
        filter_all = None
        for p in person_list:
            if labeler == 'new':
                filter = Q(labeler__name='face-identity-converted:' +  p) | Q(labeler__name='face-identity:' + p)
            else:
                filter = Q(labeler__name='face-identity-old:' +  p)
            if filter_all is None:
                filter_all = filter
            else:
                filter_all = filter_all | filter
        return filter_all
    
    if stride_face:
        labeler = 'new'
    if type(person_list) == str:
        person_list = [person_list.lower()]
    else:
        person_list = [p.lower() for p in person_list]
    
    faceIDs = FaceIdentity.objects \
              .filter(probability__gt=probability) \
              .annotate(face_size=F("face__bbox_y2") - F("face__bbox_y1")) \
              .annotate(video_id=F("face__frame__video_id")) \
    
    if not stride_face:
        faceIDs = faceIDs.exclude(face__shot__isnull=True)
    else:
        faceIDs = faceIDs.filter(face__frame__shot_boundary=False)
        
    if not person_list is None:
        if not exclude_person:
            faceIDs = faceIDs.filter(identity_filter(person_list)) 
        else:
            faceIDs = faceIDs.exclude(identity_filter(person_list)) 
                
    if not face_size is None:
        faceIDs = faceIDs.filter(face_size__gte=face_size)
        
    if not video_ids is None:
        faceIDs = faceIDs.filter(video_id__in=video_ids)
    
    if not stride_face:
        person_intrvllists = qs_to_intrvllists(
            faceIDs.annotate(video_id=F("face__shot__video_id"))
                   .annotate(shot_id=F("face__shot_id"))
                   .annotate(min_frame=F("face__shot__min_frame"))
                   .annotate(max_frame=F("face__shot__max_frame"))
                   .annotate(faceID_id=F("identity_id")),\
            schema={
                'start': 'min_frame',
                'end': 'max_frame',
                'payload': payload_type
            })
        person_intrvlcol = VideoIntervalCollection(person_intrvllists).coalesce()
    else:
        if payload_type == 'shot_id':
            payload_type = 'frame_id'
        person_intrvllists_raw = qs_to_intrvllists(
            faceIDs.annotate(video_id=F("face__frame__video_id"))
                   .annotate(frame_id=F("face__frame__number"))
                   .annotate(min_frame=F("face__frame__number"))
                   .annotate(max_frame=F("face__frame__number") + 1)
                   .annotate(faceID_id=F("identity_id")),\
            schema={
                'start': 'min_frame',
                'end': 'max_frame',
                'payload': payload_type
            })
        # dilate and coalesce
        SAMPLE_RATE = 3
        person_intrvllists = {}
        for video_id, intrvllist in person_intrvllists_raw.items():
            video = Video.objects.filter(id=video_id)[0]
            dilation = int(video.fps * SAMPLE_RATE / 2)
            person_intrvllists[video_id] = intrvllist.dilate(dilation).coalesce().dilate(-dilation)
        person_intrvlcol = VideoIntervalCollection(person_intrvllists)
        
    if granularity == 'second':
        person_intrvlcol = intrvlcol_frame2second(person_intrvlcol)
    
    print("Get {} intervals for person {}".format(count_intervals(person_intrvlcol), 
                                                  person_list[0]+' ...' if len(person_list) > 1 else person_list[0]))
    return person_intrvlcol


def get_caption_intrvlcol(phrase, video_ids=None):
    results = phrase_search(phrase, video_ids)
    
    if video_ids == None:
        videos = {v.id: v for v in Video.objects.all()}
    else:
        videos = {v.id: v for v in Video.objects.filter(id__in=video_ids).all()}
    def convert_time(k, t):
        return int(t * videos[k].fps)
    
    flattened = [
        (doc.id, convert_time(doc.id, p.start), convert_time(doc.id, p.end)) 
        for doc in results
        for p in doc.postings
    ]
    phrase_intrvllists = {}
    for video_id, t1, t2 in flattened:
        if video_id in phrase_intrvllists:
            phrase_intrvllists[video_id].append((t1, t2, 0))
        else:
            phrase_intrvllists[video_id] = [(t1, t2, 0)]
    
    for video_id, intrvllist in phrase_intrvllists.items():
        phrase_intrvllists[video_id] = IntervalList(intrvllist)
    phrase_intrvlcol = VideoIntervalCollection(phrase_intrvllists)
    print('Get {} intervals for phrase \"{}\"'.format(count_intervals(phrase_intrvlcol), phrase))
    return phrase_intrvlcol


def get_relevant_shots(intrvlcol):
    relevant_shots = set()
    for intrvllist in list(intrvlcol.get_allintervals().values()):
        for interval in intrvllist.get_intervals():
            relevant_shots.add(interval.get_payload())
    print("Get %d relevant shots" % len(relevant_shots))
    return relevant_shots


def get_numface_intrvlcol(relevant_shots, num_face=1):
    faces = Face.objects.filter(shot__in=list(relevant_shots)) \
            .annotate(video_id=F('shot__video_id')) \
            .annotate(min_frame=F('shot__min_frame')) \
            .annotate(max_frame=F('shot__max_frame'))

    # Materialize all the faces and load them into rekall with bounding box payloads
    # Then coalesce them so that all faces in the same frame are in the same interval
    # NOTE that this is slow right now since we're loading all faces!
    numface_intrvlcol = VideoIntervalCollection.from_django_qs(
        faces,
        with_payload=in_array(
            bbox_payload_parser(VideoIntervalCollection.django_accessor))
        ).coalesce(payload_merge_op=payload_plus).filter(payload_satisfies(length_exactly(num_face)))
    
    num_intrvl = 0
    for _, intrvllist in numface_intrvlcol.get_allintervals().items():
        num_intrvl += intrvllist.size()
    print("Get {} relevant {} face intervals".format(num_intrvl, num_face))
    return numface_intrvlcol


def count_face_in_shot(relevant_shots):
    faces = Face.objects.filter(shot__in=list(relevant_shots), background=False) \
            .annotate(shot_id=F('shot_id'))
    face_cnt = {}
    for face in faces:
        if not face.shot_id in face_cnt:
            face_cnt[face.shot_id] = 0
        face_cnt[face.shot_id] += 1
    return face_cnt
    
    
def get_person_phrase_intervals(person_intrvlcol, phrase, num_face=1, filter_still=True):
    phrase_intrvlcol = get_caption_intrvlcol(phrase, person_intrvlcol.get_allintervals().keys())
    
    person_phrase_intrvlcol_raw = person_intrvlcol.overlaps(phrase_intrvlcol, working_window=0)
    # only keep intervals which is the same before overlap
    person_phrase_intrvlcol = person_phrase_intrvlcol_raw.filter_against(
        phrase_intrvlcol,
        predicate = equal(),
        working_window=0)
    print('Get {} person intervals with phrase \"{}\"'.format(count_intervals(person_phrase_intrvlcol), phrase))
    
    relevant_shots = get_relevant_shots(person_phrase_intrvlcol)
    numface_intrvlcol = get_numface_intrvlcol(relevant_shots, num_face)
    person_alone_phrase_intrvlcol = person_phrase_intrvlcol.overlaps(numface_intrvlcol, working_window=0)
    
    # run optical flow to filter out still images
    intervals = intrvlcol2list(person_alone_phrase_intrvlcol)
    if not filter_still:
        return intervals
    intervals_nostill = filter_still_image_parallel(intervals)
    intervals_final = intervals_nostill if len(intervals_nostill) > 0 else intervals
    
    print('Get {} person intervals with phrase \"{}\" with {} faces'.format(len(intervals_final), phrase, num_face))
    return intervals_final
    
    
def filter_still_image_t(interval):
    video_id, sfid, efid = interval[:3]
    video = Video.objects.filter(id=video_id)[0]
    fid = (sfid + efid) // 2
    frame_first = load_frame(video, fid, [])
    frame_second = load_frame(video, fid + 1, [])
    diff = 1. * np.sum(frame_first - frame_second) / frame_first.size
#     print(video.id, fid, diff)
    return diff > 15

def filter_still_image_parallel(intervals, limit=100):
    durations = [i[-1] for i in intervals]
    if limit < len(intervals):
#         intervals = random.sample(intervals, limit)
        intervals = [intervals[idx] for idx in np.argsort(durations)[-limit : ]]
    filter_res = par_for(filter_still_image_t, intervals)
    return [intrv for i, intrv in enumerate(intervals) if filter_res[i]]
