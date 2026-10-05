"""Learning statistics from saved attempts; no inference of Go concepts or strength."""
import json
from datetime import datetime, timedelta, timezone

STUDY_TIMEZONE = timezone(timedelta(hours=8))


def attempt_record(row):
    attempt_id, point_id, raw, created = row
    result = json.loads(raw)
    return {'id': attempt_id, 'point_id': point_id, 'verdict': result['verdict'],
            'answer': result['answer'], 'loss': result['loss'],
            'completed_at': datetime.fromisoformat(result.get('completed_at', created)).astimezone(timezone.utc).isoformat(),
            'time_estimated': 'completed_at' not in result}


def summarize_study(rows, tags, available, now, period, due_count):
    today = now.astimezone(STUDY_TIMEZONE).date()
    length = 7 if period == '7' else 30
    first_day = today - timedelta(days=length-1)
    records = []
    for row in rows:
        item = attempt_record(row)
        stamp = datetime.fromisoformat(item['completed_at'])
        day = stamp.astimezone(STUDY_TIMEZONE).date()
        if stamp <= now and (period == 'all' or day >= first_day):
            records.append(dict(item, day=day.isoformat()))
    records.sort(key=lambda item: item['completed_at'])
    counts = {verdict: sum(item['verdict'] == verdict for item in records)
              for verdict in ('accepted', 'retry', 'revealed')}
    answered = counts['accepted'] + counts['retry']
    daily = []
    for offset in range(length):
        day = (first_day + timedelta(days=offset)).isoformat()
        values = {verdict: sum(item['day'] == day and item['verdict'] == verdict for item in records)
                  for verdict in counts}
        daily.append(dict(day=day, **values))
    # One latest result per card prevents repeated practice of one card from
    # appearing to provide evidence about many different positions.
    latest = {}
    for item in records:
        if item['point_id'] in available:
            latest[item['point_id']] = item
    topics = []
    for tag in sorted({tag for point_id in available for tag in tags.get(point_id, [])}):
        samples = [item for point_id, item in latest.items() if tag in tags.get(point_id, [])]
        needs_work = sum(item['verdict'] != 'accepted' for item in samples)
        topics.append({'tag': tag, 'sample_count': len(samples), 'needs_work': needs_work,
                       'sufficient': len(samples) >= 3,
                       'card_count': sum(tag in tags.get(point_id, []) for point_id in available)})
    topics.sort(key=lambda item: (not item['sufficient'], -item['needs_work']/max(1, item['sample_count']), -item['sample_count'], item['tag']))
    return {'period': period, 'timezone': 'Asia/Shanghai', 'start_day': None if period == 'all' else first_day.isoformat(),
            'end_day': today.isoformat(), 'due_count': due_count,
            'summary': dict(counts, completed=len(records), answered=answered,
                            pass_rate=round(counts['accepted']/answered*100, 1) if answered else None,
                            practiced_points=len({item['point_id'] for item in records}),
                            active_days=len({item['day'] for item in records}),
                            estimated_times=sum(item['time_estimated'] for item in records)),
            'daily': daily, 'topics': topics,
            'untagged_count': sum(not tags.get(point_id) for point_id in available)}
