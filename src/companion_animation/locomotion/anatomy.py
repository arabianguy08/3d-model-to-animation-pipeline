"""Anatomical role mapping for compatible biped hierarchies."""
CHAIN_ROLES = ('Spine', 'Spine1', 'Spine2', 'Neck')
END_ALIASES = {'toe_end': 'Toe_End', 'head_end': 'HeadTop_End', 'headtop_end': 'HeadTop_End'}

def canon(name):
    for prefix in ('mixamorig:', 'mixamorig'):
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def anatomical_names(bones, overrides=None):
    """Maps a Mixamo-family skeleton's bone names onto anatomical roles by hierarchy, not by name.

    The shared Mixamo motion files name their spine Spine02 (lowest) -> Spine01 -> Spine (chest),
    the reverse of the shipped rigs' Spine -> Spine1 -> Spine2, so the spine/neck chain is taken
    from the actual parent path between Hips and Head. bones: {name: parent_name}."""
    names = {n: (overrides or {}).get(n, canon(n)) for n in bones}
    lower = {v.lower(): k for k, v in names.items()}
    hips, head = lower.get('hips'), lower.get('head')
    if not hips or not head:
        raise ValueError('Skeleton lacks an identifiable Hips/Head pair')
    chain, node = [], bones[head]
    while node and node != hips:
        if node in chain or node not in bones:
            raise ValueError('Broken or cyclic anatomical hierarchy')
        chain.append(node)
        node = bones[node]
    if node != hips or len(chain) != len(CHAIN_ROLES):
        raise ValueError(f'Hips->Head chain {chain[::-1]} does not have {len(CHAIN_ROLES)} spine/neck joints')
    roles = {n: n_c for n, n_c in names.items()}
    for bone, role in zip(chain[::-1], CHAIN_ROLES):
        roles[bone] = role
    for bone, c in names.items():
        for suffix, role in END_ALIASES.items():
            if c.lower().endswith(suffix):
                roles[bone] = c[:len(c) - len(suffix)] + role
    roles[hips], roles[head] = 'Hips', 'Head'
    if len(set(roles.values())) != len(roles):
        raise ValueError('Ambiguous anatomical role mapping')
    return roles, [names[c] for c in chain[::-1]]
