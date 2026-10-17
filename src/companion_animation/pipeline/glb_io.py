"""Small glTF binary reader/writer. No external dependencies."""
import json
import struct
from pathlib import Path


def read(path):
    data = Path(path).read_bytes()
    magic, version, length = struct.unpack_from('<4sII', data)
    if magic != b'glTF' or version != 2 or length != len(data):
        raise ValueError('Invalid GLB header/length')
    document, binary, offset = None, b'', 12
    while offset < length:
        size, kind = struct.unpack_from('<I4s', data, offset)
        offset += 8
        chunk = data[offset:offset + size]
        if len(chunk) != size:
            raise ValueError('Truncated GLB chunk')
        if kind == b'JSON':
            document = json.loads(chunk)
        elif kind == b'BIN\x00':
            binary = chunk
        offset += size
    if document is None:
        raise ValueError('Missing GLB JSON')
    return document, bytearray(binary)


def write(path, document, binary):
    document['buffers'] = [{'byteLength': len(binary)}]
    encoded = json.dumps(document, separators=(',', ':'), allow_nan=False).encode()
    encoded += b' ' * (-len(encoded) % 4)
    binary = bytes(binary) + b'\x00' * (-len(binary) % 4)
    chunks = struct.pack('<I4s', len(encoded), b'JSON') + encoded
    if binary:
        chunks += struct.pack('<I4s', len(binary), b'BIN\x00') + binary
    Path(path).write_bytes(struct.pack('<4sII', b'glTF', 2, 12 + len(chunks)) + chunks)


def accessor(document, binary, index):
    entry = document['accessors'][index]
    if 'sparse' in entry:
        raise ValueError('Sparse accessor requires explicit support')
    sizes = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT4': 16}
    formats = {5120: 'b', 5121: 'B', 5122: 'h', 5123: 'H', 5125: 'I', 5126: 'f'}
    width, code = sizes[entry['type']], formats[entry['componentType']]
    row = struct.Struct('<' + code * width)
    view = document['bufferViews'][entry['bufferView']]
    offset = view.get('byteOffset', 0) + entry.get('byteOffset', 0)
    stride = view.get('byteStride', row.size)
    return [row.unpack_from(binary, offset + i * stride) for i in range(entry['count'])]


def append_float(document, binary, values, kind):
    width = {'SCALAR': 1, 'VEC3': 3, 'VEC4': 4}[kind]
    binary.extend(b'\x00' * (-len(binary) % 4))
    offset = len(binary)
    flat = [x for row in values for x in row] if width > 1 else list(values)
    binary.extend(struct.pack('<' + 'f' * len(flat), *flat))
    views = document.setdefault('bufferViews', [])
    views.append({'buffer': 0, 'byteOffset': offset, 'byteLength': len(flat) * 4})
    entries = document.setdefault('accessors', [])
    entry = {'bufferView': len(views) - 1, 'componentType': 5126,
             'count': len(flat) // width, 'type': kind}
    if kind == 'SCALAR':
        entry.update(min=[min(flat)], max=[max(flat)])
    entries.append(entry)
    return len(entries) - 1
