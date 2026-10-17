"""Tolerance-based bind identity matching, including collapsed preview geometry."""
import math
from itertools import product


def distance(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def fields_match(a, b, weight_tolerance, morph_tolerance):
    aw, bw = dict(a['weights']), dict(b['weights'])
    if aw.keys() != bw.keys() or any(abs(aw[n] - bw[n]) > weight_tolerance for n in aw):
        return False
    am, bm = dict(a['morphs']), dict(b['morphs'])
    return am.keys() == bm.keys() and all(distance(am[n], bm[n]) <= morph_tolerance for n in am)


def correspondence(source, target, position_tolerance=1e-6, local_compatible=True):
    """Match all vertices by geometry, named skin weights and named morph deltas.

    Never pick merely the closest anatomical candidate. Multiple candidates must
    be identical within float encoding precision. Equivalent UV seam duplicates
    count as represented; genuinely omitted source vertices still fail.
    """
    if position_tolerance <= 0:
        raise ValueError('Correspondence tolerance must be positive')
    buckets = {}
    cell = lambda p: tuple(math.floor(x / position_tolerance) for x in p)
    for index, row in enumerate(source):
        buckets.setdefault(cell(row['position']), []).append(index)
    mapped, covered = [], set()
    for row in target:
        center = cell(row['position'])
        candidates = []
        for offset in product((-1, 0, 1), repeat=3):
            for index in buckets.get(tuple(x + y for x, y in zip(center, offset)), ()):
                original = source[index]
                if distance(original['position'], row['position']) > position_tolerance:
                    continue
                if local_compatible and distance(original['local'], row['local']) > 1e-6:
                    continue
                if fields_match(original, row, 1e-6, 1e-6):
                    candidates.append(index)
        if not candidates:
            raise ValueError('Reopened neutral/skin/morph vertex identity differs')
        first = source[candidates[0]]
        for index in candidates[1:]:
            other = source[index]
            if (distance(first['local'], other['local']) > 1e-7 or
                    not fields_match(first, other, 1e-7, 1e-7)):
                raise ValueError('Anatomically ambiguous reopened vertex correspondence')
        mapped.append(next((index for index in candidates if index not in covered), candidates[0]))
        covered.update(candidates)
    if covered != set(range(len(source))):
        raise ValueError('Source vertices omitted from persisted GLB')
    return mapped
