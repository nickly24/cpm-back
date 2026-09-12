"""Executable specification checks, NOT application/API/DB tests.

Uses only the standard library. The model independently exercises the rules in
FEX-05/07/09/10/15 and checks the Markdown package for broken links/JSON examples.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from datetime import datetime, timedelta, timezone
from itertools import permutations, product
from pathlib import Path
import json
import random
import re

ROOT = Path(__file__).resolve().parents[1]


def check_documents():
    errors = []
    files = sorted(ROOT.rglob('*.md'))
    json_count = 0
    for path in files:
        body = path.read_text(encoding='utf-8')
        for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', body):
            if target.startswith(('https:', 'http:', '#')):
                continue
            target = target.split('#', 1)[0]
            if target and not (path.parent / target).resolve().exists():
                errors.append(f'{path.name}: broken link {target}')
        if len(re.findall(r'^```', body, flags=re.M)) % 2:
            errors.append(f'{path.name}: unbalanced fences')
        for block in re.findall(r'^```json\s*\n(.*?)^```', body, re.M | re.S):
            try:
                json.loads(block)
                json_count += 1
            except json.JSONDecodeError as exc:
                errors.append(f'{path.name}: invalid JSON example: {exc}')
    for prefix in ('tasks', 'development'):
        ids = sorted(p.name[:6] for p in (ROOT / prefix).glob('FEX-*.md'))
        assert ids == [f'FEX-{i:02}' for i in range(1, 17)], (prefix, ids)
    scenario_ids = re.findall(r'^\| (EX-\d+) \|', (ROOT / '03-acceptance-scenarios.md').read_text(), re.M)
    assert scenario_ids == [f'EX-{i:02}' for i in range(1, 65)]
    assert not errors, '\n'.join(errors)
    return {'markdown_files': len(files), 'json_examples': json_count}


def check_results_and_time():
    def effective(attempts):
        completed = [a for a in attempts if a['completed']]
        if not completed:
            return None
        current = max(completed, key=lambda a: a['number'])
        return current['appeals'][-1] if current['appeals'] else current['grade']

    first = {'number': 1, 'completed': True, 'grade': 5, 'appeals': []}
    retake = {'number': 2, 'completed': False, 'grade': 1, 'appeals': []}
    assert effective([]) is None
    assert effective([first, retake]) == 5
    retake['completed'] = True
    assert effective([first, retake]) == 1
    first['appeals'] = [4]
    assert effective([first, retake]) == 1
    retake['appeals'] = [1, 0]
    assert effective([first, retake]) == 0  # zero is a result, not absence
    moscow = timezone(timedelta(hours=3))
    start = datetime(2026, 9, 12, 0, 30, tzinfo=moscow)
    assert start.astimezone(timezone.utc).date().isoformat() == '2026-09-11'
    assert start.date().isoformat() == '2026-09-12'
    for delta, included in ((-1, False), (0, True), (1, True)):
        now = start + timedelta(microseconds=delta)
        assert (start <= now) is included
    return 10


def check_scoring():
    checks = 0
    for weights in product(range(1, 5), repeat=3):
        maximum = sum(weights)
        for votes in product((Decimal(0), Decimal('.5'), Decimal(1)), repeat=3):
            raw = sum(v * w for v, w in zip(votes, weights))
            assert 0 <= raw <= maximum
            assert raw * 2 == (raw * 2).to_integral_value()
            low = int(raw.to_integral_value(rounding=ROUND_FLOOR))
            high = int(raw.to_integral_value(rounding=ROUND_CEILING))
            assert 0 <= low <= high <= maximum
            checks += 1
    thresholds = [0, 1, 2, 3, 4, 6]
    grade = lambda score: max(i for i, minimum in enumerate(thresholds) if score >= minimum)
    assert grade(5) == 4 and grade(6) == 5
    raw = Decimal('2.5')
    for _ in range(1000):  # half extra: repeat never changes the regular total
        assert raw == Decimal('2.5')
    assert int(raw.to_integral_value(rounding=ROUND_CEILING)) == 3
    exam_average = Decimal(10) / 3
    final = Decimal(80) * Decimal('.25') + exam_average * 6 + Decimal(60) * Decimal('.45')
    assert final == 67
    assert exam_average != (Decimal('2.5') + 5) / 2
    return checks


class SelectionModel:
    def __init__(self, sizes, quotas, replacements, rng):
        self.bank = {p: [(p, q) for q in range(n)] for p, n in enumerate(sizes)}
        self.remaining = dict(enumerate(quotas))
        self.cycles = {p: 1 for p in self.bank}
        self.used_cycle = {}
        self.seen = set()
        self.banned = set()
        self.last = None
        self.budget = replacements
        self.rng = rng
        self.presentations = 0

    def show(self, candidate, cycle):
        assert candidate not in self.banned and candidate != self.last
        self.seen.add(candidate)
        self.last = candidate
        self.used_cycle[candidate] = cycle
        self.cycles[candidate[0]] = cycle
        self.presentations += 1
        return candidate

    def regular(self, part=None):
        if part is None:
            slots = [p for p, n in self.remaining.items() for _ in range(n)]
            part = self.rng.choice(slots)
        eligible = [q for q in self.bank[part] if q not in self.seen]
        assert eligible, ('regular exhausted', self.bank, self.seen, self.budget)
        return self.show(self.rng.choice(eligible), 1)

    def extra(self, source=None):
        options = {}
        for part in ([source] if source is not None else self.bank):
            available = [q for q in self.bank[part] if q not in self.banned]
            cycle = self.cycles[part]
            unused = [q for q in available if self.used_cycle.get(q) != cycle]
            if not unused:
                cycle += 1
                unused = available
            candidates = [q for q in unused if q != self.last]
            if candidates:
                options[part] = (candidates, cycle)
        assert options, ('extra exhausted', self.bank, self.banned, self.last, source)
        part = self.rng.choice(list(options))
        candidates, cycle = options[part]
        return self.show(self.rng.choice(candidates), cycle)

    def replace(self, current, purpose):
        assert self.budget > 0 and current == self.last
        self.banned.add(current)
        self.budget -= 1
        new = self.regular(current[0]) if purpose == 'regular' else self.extra(current[0])
        assert new[0] == current[0] and new != current
        return new


def check_selection():
    trajectories = presentations = 0
    for part_count in (1, 2, 3):
        for sizes in product(range(1, 5), repeat=part_count):
            for quotas in product((1, 2), repeat=part_count):
                for replacements in (0, 1, 2):
                    if any(n < q + replacements for n, q in zip(sizes, quotas)):
                        continue
                    for source in (None, *range(part_count)):
                        reserve = sum(sizes) if source is None else sizes[source]
                        if reserve < replacements + 2:
                            continue
                        for seed in (0, 1, 2):
                            m = SelectionModel(sizes, quotas, replacements, random.Random(seed))
                            awarded = {}
                            while sum(m.remaining.values()):
                                question = m.regular()
                                while m.budget and m.rng.random() < .35:
                                    question = m.replace(question, 'regular')
                                awarded[question] = 1
                                m.remaining[question[0]] -= 1
                            expected_awarded = sum(awarded.values())
                            for _ in range(50):
                                question = m.extra(source)
                                while m.budget and m.rng.random() < .35:
                                    question = m.replace(question, 'extra')
                                assert sum(awarded.values()) == expected_awarded
                            trajectories += 1
                            presentations += m.presentations
    assert trajectories > 1000
    return {'trajectories': trajectories, 'presentations': presentations}


def check_vote_orders():
    count = 0
    for values in ((0,) * 6, (.5,) * 6, (1,) * 6, (0, 1, .5, 1, 0, .5)):
        for order in permutations(range(6)):
            votes = {}
            for actor in order:
                assert actor not in votes
                votes[actor] = values[actor]
            outcome = next(iter(votes.values())) if len(set(votes.values())) == 1 else 'disputed'
            expected = values[0] if len(set(values)) == 1 else 'disputed'
            assert outcome == expected and len(votes) == 6
            count += 1
    return count


if __name__ == '__main__':
    result = {'documents': check_documents(), 'score_cases': check_scoring(),
              'result_and_time_cases': check_results_and_time(),
              'selection': check_selection(), 'vote_orders': check_vote_orders()}
    print(json.dumps(result, ensure_ascii=False, indent=2))
