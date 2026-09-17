"""Per-part grading revisions. Ownership/geometry are validated by the caller."""
import copy


def result_percentage(entry):
    for field in ('combined_score', 'score'):
        if entry.get(field) is not None:
            return float(entry[field])
    raw = float(entry.get('combined_raw_score', entry.get('raw_score', 0)) or 0)
    maximum = float(entry.get('combined_max_score', entry.get('max_total_score', 0)) or 0)
    return raw / maximum * 100 if maximum else 0.0


def result_rank(entry, entries):
    """Competition ranking shared by printing and lecture exports (1, 1, 3)."""
    score = result_percentage(entry)
    return 1 + sum(result_percentage(other) > score for other in entries if isinstance(other, dict))


def changed_parts(saved_rules, current_rules):
    return {part_id for part_id in set(saved_rules) | set(current_rules)
            if saved_rules.get(part_id) != current_rules.get(part_id)}


def reconcile_scores(payload, current_rules):
    """Invalidate only affected machine marks; preserve evidence and human work.

    A changed maximum also needs human review: silently clamping an old manual
    mark would change the teacher's judgment. Record dictionaries and the scores
    mapping retain their identities for callbacks in the live grading window.
    """
    saved_rules = payload['subjective_part_rules']
    changed = changed_parts(saved_rules, current_rules)
    for records in payload.get('scores', {}).values():
        if not isinstance(records, dict):
            continue
        for part_id in changed:
            record = records.get(part_id)
            if not isinstance(record, dict):
                continue
            old_rule = saved_rules.get(part_id) or {}
            new_rule = current_rules.get(part_id) or {}
            maximum_changed = old_rule.get('score') != new_rule.get('score')
            if record.get('manual_graded') and not maximum_changed:
                continue
            if record.get('score') is not None:
                previous = copy.deepcopy(record)
                previous.pop('grading_history', None)
                record.setdefault('grading_history', []).append({
                    'rule': copy.deepcopy(old_rule), 'record': previous,
                })
            record.update(score=None, auto_graded=False, manual_graded=False,
                          ocr_auto_need_manual=True,
                          ocr_auto_reason='本空评分规则已更改，待重新判分；原记录已保留')
    payload['subjective_part_rules'] = copy.deepcopy(current_rules)
    payload['version'] = 2
    return changed
