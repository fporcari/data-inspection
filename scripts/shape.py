#!/usr/bin/env python3
"""Print the shape of a data file without any of its values.

Usage:
    python3 <skill>/scripts/shape.py <file> [--max-paths N] [--sample N]

Structure only: element paths, attribute names, headers, keys, value classes
(int, decimal, date, email-like, text…), length ranges and counts. Nothing read
from a value is printed, so the fields can be classified before a single value
has reached the model. A dict keyed by data — more than 20 keys, or keys that do
not read as field names (spaces, `@`, three or more digits: tax ids, codes, emails)
— has its keys reported as a field of their own, `{keys}`, never as paths.

With Presidio available (the skill venv), each field also gets a profile of the
personal data found in a sample of its values — entity types and how many sampled
values carry them, never the values.

Formats: xml (and the xml inside a signed .p7m), csv/tsv, json/jsonl, xlsx
(needs openpyxl), sql dumps. Anything else gets size only.

Exit codes:
    0  shape printed
    1  missing or unreadable file
    2  format not recognised, or its content could not be parsed
"""
import argparse
import collections
import csv
import io
import json
import pathlib
import re
import sys
import xml.etree.ElementTree as ET

from common import (classify, detect, is_header, is_map, load_analyzer, local_name,
                    sniff_delimiter, use_skill_venv, xml_bytes)

try:
    from openpyxl import load_workbook
except Exception:
    load_workbook = None

PROFILED_CLASSES = {'text', 'int', 'email-like'}


class Field:
    sample_size = 50

    def __init__(self):
        self.count = 0
        self.classes = collections.Counter()
        self.lengths = []
        self.sample = []
        self.profile = None

    def add(self, value):
        self.count += 1
        if value is None:
            self.classes['null'] += 1
            return
        if isinstance(value, bool):
            self.classes['bool'] += 1
            return
        if isinstance(value, (int, float)):
            self.classes['int' if isinstance(value, int) else 'decimal'] += 1
            return
        text = str(value).strip()
        kind = classify(text)
        self.lengths.append(len(text))
        self.classes[kind] += 1
        if kind in PROFILED_CLASSES and len(self.sample) < self.sample_size:
            self.sample.append(text)

    def profile_with(self, analyzer):
        self.profile = collections.Counter()
        for text in self.sample:
            self.profile.update({r.entity_type for r in detect(analyzer, text)})
        sampled = len(self.sample)
        self.sample = []
        return sampled

    def describe(self):
        classes = ', '.join(f'{name}×{n}' for name, n in self.classes.most_common())
        if self.lengths:
            classes += f'  len {min(self.lengths)}-{max(self.lengths)}'
        return f'{self.count:>7}  {classes}'

    def describe_profile(self, sampled):
        if not self.profile:
            return ''
        return '  [' + ', '.join(f'{kind} {n}/{sampled}'
                                 for kind, n in self.profile.most_common()) + ']'


def shape_xml(path, raw):
    fields = collections.defaultdict(Field)
    stack, root = [], None
    for event, element in ET.iterparse(io.BytesIO(xml_bytes(path, raw)), events=('start', 'end')):
        if event == 'start':
            stack.append(local_name(element.tag))
            if root is None:
                root = element.tag
            continue
        node = '/' + '/'.join(stack)
        if len(element) == 0:
            fields[node].add(element.text)
        for name, value in element.attrib.items():
            fields[f'{node}/@{local_name(name)}'].add(value)
        stack.pop()
        element.clear()
    namespace = root[1:].split('}', 1)[0] if root and root.startswith('{') else '-'
    return f'root {local_name(root or "?")}  namespace {namespace}', fields


def shape_csv(path, raw):
    text = raw.decode('utf-8-sig', errors='replace')
    delimiter = sniff_delimiter([line for line in text.splitlines()[:50] if line.strip()])
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    first, rest = (rows[0], rows[1:]) if rows else ([], [])
    header = is_header(first, rest)
    names = first if header else [f'col{i + 1}' for i in range(len(first))]
    fields = collections.OrderedDict((name, Field()) for name in names)
    for row in rest if header else rows:
        for name, value in zip(names, row):
            fields[name].add(value)
    verdict = 'header row' if header else 'header not certain: columns numbered, ask the user'
    return f'delimiter {delimiter!r}  {verdict}', fields


def walk_json(value, node, fields):
    if isinstance(value, dict):
        if is_map(value):
            for key, item in value.items():
                fields[f'{node}{{keys}}'].add(key)
                walk_json(item, f'{node}{{*}}', fields)
            return
        for key, item in value.items():
            walk_json(item, f'{node}.{key}', fields)
    elif isinstance(value, list):
        for item in value:
            walk_json(item, f'{node}[]', fields)
    else:
        fields[node or '$'].add(value)


def shape_json(path, raw):
    fields = collections.defaultdict(Field)
    text = raw.decode('utf-8-sig', errors='replace')
    if path.suffix.lower() == '.jsonl':
        lines = [line for line in text.splitlines() if line.strip()]
        for line in lines:
            walk_json(json.loads(line), '', fields)
        return f'{len(lines)} records', fields
    walk_json(json.loads(text), '', fields)
    return 'single document', fields


def shape_xlsx(path, raw):
    if load_workbook is None:
        raise ImportError('openpyxl')
    fields = collections.OrderedDict()
    sheets = []
    for sheet in load_workbook(io.BytesIO(raw), read_only=True, data_only=True).worksheets:
        rows = sheet.iter_rows(values_only=True)
        names = [str(name) if name is not None else f'col{i + 1}'
                 for i, name in enumerate(next(rows, ()))]
        sheets.append(sheet.title)
        for row in rows:
            for name, value in zip(names, row):
                fields.setdefault(f'{sheet.title}!{name}', Field()).add(value)
    return f'sheets {", ".join(sheets)}  (first row taken as header)', fields


def shape_sql(path, raw):
    text = raw.decode('utf-8', errors='replace')
    fields = collections.OrderedDict()
    for match in re.finditer(r'CREATE TABLE\s+(?:IF NOT EXISTS\s+)?([\w."]+)\s*\((.*?)\);', text, re.S | re.I):
        for line in match.group(2).splitlines():
            column = line.strip().rstrip(',').split()
            if column and column[0].upper() not in ('PRIMARY', 'CONSTRAINT', 'UNIQUE', 'FOREIGN', 'CHECK'):
                fields.setdefault(f'{match.group(1)}.{column[0]}', Field()).classes[' '.join(column[1:3])] += 1
    inserts = collections.Counter(re.findall(r'INSERT INTO\s+([\w."]+)', text, re.I))
    copies = collections.Counter(re.findall(r'^COPY\s+([\w."]+)', text, re.M | re.I))
    counts = ', '.join(f'{t}: {n} inserts' for t, n in inserts.items())
    counts += ', '.join(f'{t}: COPY block' for t in copies)
    return f'tables {len({k.rsplit(".", 1)[0] for k in fields})}  {counts or "no data statements"}', fields


SHAPERS = {'.xml': shape_xml, '.p7m': shape_xml, '.csv': shape_csv, '.tsv': shape_csv,
           '.txt': shape_csv, '.json': shape_json, '.jsonl': shape_json,
           '.xlsx': shape_xlsx, '.sql': shape_sql}


def main():
    use_skill_venv(__file__)
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('file')
    parser.add_argument('--max-paths', type=int, default=300)
    parser.add_argument('--sample', type=int, default=Field.sample_size)
    args = parser.parse_args()
    Field.sample_size = args.sample
    path = pathlib.Path(args.file)
    try:
        raw = path.read_bytes()
    except OSError as error:
        print(f'cannot read {path}: {error.strerror}', file=sys.stderr)
        return 1
    print(f'{path.name}  {len(raw):,} bytes')
    shaper = SHAPERS.get(path.suffix.lower())
    if shaper is None:
        print('format not recognised: classify it with the user')
        return 2
    try:
        summary, fields = shaper(path, raw)
    except Exception as error:
        print(f'{shaper.__name__} could not parse it ({type(error).__name__}): '
              f'classify it with the user')
        return 2
    print(summary)
    analyzer = load_analyzer()
    print(f'personal-data profile: presidio, up to {args.sample} values per field' if analyzer
          else 'personal-data profile: off (presidio not available)')
    shown = list(fields.items())[:args.max_paths]
    for name, field in shown:
        sampled = field.profile_with(analyzer) if analyzer else 0
        print(f'{field.describe()}  {name}{field.describe_profile(sampled)}')
    if len(fields) > args.max_paths:
        print(f'… {len(fields) - args.max_paths} more paths (--max-paths)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
