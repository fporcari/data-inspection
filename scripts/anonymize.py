#!/usr/bin/env python3
"""Write anonymised copies of data files, driven by a field mapping.

Usage:
    python3 <skill>/scripts/anonymize.py --map MAP.json --out-dir DIR FILE [FILE ...]

MAP.json maps field paths, exactly as shape.py prints them, to one action:
    keep            the value stays
    drop            the element, attribute, column or key goes
    pseudo[:TYPE]   a consistent pseudonym; TYPE (IT_FISCAL_CODE, IT_VAT_CODE,
                    IBAN_CODE, EMAIL_ADDRESS, PHONE_NUMBER, PERSON, LOCATION…) is
                    guessed from the value when omitted
    text            free text: Presidio finds the entities and pseudonymises them

A field the map leaves out stops the run before anything is written, unless
--default keep|drop decides for it. One run holds one random key, in memory only:
the same value gets the same pseudonym across every file of the run, and another
one in the next run. Tax ids, VAT numbers and IBANs come out valid (check
characters recomputed; an Italian IBAN's CIN is not), so the program under test
validates them as it would the originals.

With Presidio available, the values of `keep` fields are scanned once more and
any personal data still found there is reported, as counts.

Prints counts per field, never a value. Formats: xml (a .p7m comes out as
unsigned xml), csv/tsv, json/jsonl.

Exit codes:
    0  copies written
    1  unreadable input, bad map, or fields the map leaves out
    2  an action needs Presidio and it is not available
"""
import argparse
import collections
import csv
import hashlib
import hmac
import io
import json
import pathlib
import re
import secrets
import sys
import xml.etree.ElementTree as ET

from common import (CF, IBAN, EMAIL_LIKE, cf_check, detect, iban_valid, is_header, is_map,
                    load_analyzer, local_name, sniff_delimiter, use_skill_venv,
                    vat_check, xml_bytes)

try:
    from presidio_anonymizer import AnonymizerEngine
    from presidio_anonymizer.entities import OperatorConfig
except ImportError:
    AnonymizerEngine = None

KEY = secrets.token_bytes(32)
CONSONANTS = 'BCDFGHLMNPRSTVZ'
LABELS = {'PERSON': 'Persona', 'LOCATION': 'Luogo', 'ORGANIZATION': 'Organizzazione',
          'NRP': 'Gruppo', 'DATE_TIME': 'Data'}
RESIDUE_SAMPLE = 200


class Unavailable(Exception):
    pass


def digest(kind, value):
    return hmac.new(KEY, f'{kind}\0{value}'.encode(), hashlib.sha256).digest()


def fake_cf(value):
    d = digest('cf', value.upper())
    body = (''.join(CONSONANTS[b % len(CONSONANTS)] for b in d[:6])
            + f'{d[6] % 100:02d}' + 'ABCDEHLMPRST'[d[7] % 12]
            + f'{d[8] % 28 + 1 + (40 if d[9] % 2 else 0):02d}' + 'Z999')
    return body + cf_check(body)


def fake_vat(value):
    digits = ''.join(str(b % 10) for b in digest('vat', value)[:10])
    return digits + vat_check(digits)


def fake_iban(value):
    compact = value.replace(' ', '').upper()
    country = compact[:2]
    d = digest('iban', compact)
    if country == 'IT':
        bban = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'[d[0] % 26] + ''.join(str(b % 10) for b in d[1:23])
    else:
        bban = ''.join(str(b % 10) for b in d[:len(compact) - 4])
    check = 98 - int(''.join(str(int(c, 36)) for c in bban + country + '00')) % 97
    return f'{country}{check:02d}{bban}'


def guess(value):
    compact = value.replace(' ', '').upper()
    if CF.match(compact) and cf_check(compact[:15]) == compact[15]:
        return 'IT_FISCAL_CODE'
    if re.fullmatch(r'\d{11}', compact) and vat_check(compact[:10]) == compact[10]:
        return 'IT_VAT_CODE'
    if IBAN.match(compact) and iban_valid(compact):
        return 'IBAN_CODE'
    if EMAIL_LIKE.match(value.strip()):
        return 'EMAIL_ADDRESS'
    return 'VALUE'


def pseudo(value, kind=None):
    if value is None or not str(value).strip():
        return value
    value = str(value)
    kind = kind or guess(value)
    tag = digest(kind, value.strip().upper()).hex()[:6]
    if kind == 'IT_FISCAL_CODE':
        return fake_cf(value.strip())
    if kind == 'IT_VAT_CODE':
        return fake_vat(value.strip())
    if kind == 'IBAN_CODE':
        return fake_iban(value)
    if kind == 'EMAIL_ADDRESS':
        return f'utente_{tag}@esempio.invalid'
    if kind == 'PHONE_NUMBER':
        return '+39 000 ' + ''.join(str(b % 10) for b in digest(kind, value)[:7])
    return f'{LABELS.get(kind, "Valore")}_{tag}'


class Run:
    def __init__(self, mapping, default, analyzer):
        self.mapping = mapping
        self.default = default
        self.analyzer = analyzer
        self.anonymizer = AnonymizerEngine() if AnonymizerEngine and analyzer else None
        self.counts = collections.Counter()
        self.entities = collections.Counter()
        self.residue = collections.defaultdict(collections.Counter)
        self.kept = collections.Counter()
        self.missing = set()

    def action(self, path):
        action = self.mapping.get(path, self.default)
        if action is None:
            self.missing.add(path)
            return 'keep'
        return action

    def apply(self, path, value):
        """The new value, or Run.DROP."""
        action = self.action(path)
        name, _, kind = action.partition(':')
        self.counts[(name, path)] += 1
        if name == 'drop':
            return Run.DROP
        if name == 'pseudo':
            return pseudo(value, kind or None)
        if name == 'text':
            return self.clean(path, value)
        if name != 'keep':
            raise ValueError(f'unknown action {action!r} for {path}')
        if self.analyzer and isinstance(value, str) and value.strip() and self.kept[path] < RESIDUE_SAMPLE:
            self.kept[path] += 1
            self.residue[path].update({r.entity_type for r in detect(self.analyzer, value)})
        return value

    def clean(self, path, value):
        if not self.anonymizer:
            raise Unavailable(f'{path}: text needs presidio; map it to drop')
        if value is None or not str(value).strip():
            return value
        found = detect(self.analyzer, str(value))
        for result in found:
            self.entities[(path, result.entity_type)] += 1
        operators = {kind: OperatorConfig('custom', {'lambda': lambda s, k=kind: pseudo(s, k)})
                     for kind in {r.entity_type for r in found}}
        return self.anonymizer.anonymize(text=str(value), analyzer_results=found,
                                         operators=operators).text


Run.DROP = object()


def anonymize_xml(run, path, raw):
    data = xml_bytes(path, raw)
    for _, (prefix, uri) in ET.iterparse(io.BytesIO(data), events=('start-ns',)):
        ET.register_namespace(prefix, uri)
    root = ET.fromstring(data)

    def walk(element, parent, stack):
        stack = stack + [local_name(element.tag)]
        node = '/' + '/'.join(stack)
        if run.mapping.get(node) == 'drop' and len(element):
            run.counts[('drop', node)] += 1
            parent.remove(element)
            return
        for name in list(element.attrib):
            new = run.apply(f'{node}/@{local_name(name)}', element.attrib[name])
            if new is Run.DROP:
                del element.attrib[name]
            else:
                element.attrib[name] = new
        if len(element) == 0:
            new = run.apply(node, element.text)
            if new is Run.DROP:
                parent.remove(element)
                return
            element.text = new
        for child in list(element):
            walk(child, element, stack)

    walk(root, None, [])
    out = io.BytesIO()
    ET.ElementTree(root).write(out, xml_declaration=True, encoding='UTF-8')
    return out.getvalue(), '.xml'


def anonymize_csv(run, path, raw):
    text = raw.decode('utf-8-sig', errors='replace')
    delimiter = sniff_delimiter([line for line in text.splitlines()[:50] if line.strip()])
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    first, rest = (rows[0], rows[1:]) if rows else ([], [])
    header = is_header(first, rest)
    names = first if header else [f'col{i + 1}' for i in range(len(first))]
    out = io.StringIO()
    writer = csv.writer(out, delimiter=delimiter, lineterminator='\n')
    kept = [i for i, name in enumerate(names) if run.mapping.get(name, run.default) != 'drop']
    if header:
        writer.writerow([names[i] for i in kept])
    for row in rest if header else rows:
        values = [run.apply(name, value) for name, value in zip(names, row)]
        writer.writerow([values[i] for i in kept if i < len(values)])
    return out.getvalue().encode('utf-8'), path.suffix


def walk_json(run, value, node):
    if isinstance(value, dict):
        result = {}
        keyed = is_map(value)
        for key, item in value.items():
            if keyed:
                new_key = run.apply(f'{node}{{keys}}', key)
                if new_key is Run.DROP:
                    continue
                child = walk_json(run, item, f'{node}{{*}}')
            else:
                new_key, child = key, walk_json(run, item, f'{node}.{key}')
            if child is not Run.DROP:
                result[new_key] = child
        return result
    if isinstance(value, list):
        return [child for child in (walk_json(run, item, f'{node}[]') for item in value)
                if child is not Run.DROP]
    return run.apply(node or '$', value)


def anonymize_json(run, path, raw):
    text = raw.decode('utf-8-sig', errors='replace')
    if path.suffix.lower() == '.jsonl':
        lines = [json.dumps(walk_json(run, json.loads(line), ''), ensure_ascii=False)
                 for line in text.splitlines() if line.strip()]
        return ('\n'.join(lines) + '\n').encode('utf-8'), path.suffix
    return json.dumps(walk_json(run, json.loads(text), ''), ensure_ascii=False,
                      indent=2).encode('utf-8'), path.suffix


WRITERS = {'.xml': anonymize_xml, '.p7m': anonymize_xml, '.csv': anonymize_csv,
           '.tsv': anonymize_csv, '.txt': anonymize_csv, '.json': anonymize_json,
           '.jsonl': anonymize_json}


def report(run):
    for (action, field), count in sorted(run.counts.items(), key=lambda item: item[0][1]):
        found = ', '.join(f'{kind}×{n}' for (path, kind), n in run.entities.items() if path == field)
        print(f'  {action:<7}{count:>7}  {field}' + (f'  [{found}]' if found else ''))
    for field, kinds in run.residue.items():
        if kinds:
            print(f'  residue in keep field {field}: '
                  + ', '.join(f'{k} {n}/{run.kept[field]}' for k, n in kinds.most_common()))


def main():
    use_skill_venv(__file__)
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('files', nargs='+')
    parser.add_argument('--map', required=True)
    parser.add_argument('--out-dir', required=True)
    parser.add_argument('--default', choices=('keep', 'drop'))
    args = parser.parse_args()
    try:
        mapping = json.loads(pathlib.Path(args.map).read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        print(f'cannot read the map: {type(error).__name__}', file=sys.stderr)
        return 1
    bad = sorted(path for path, action in mapping.items()
                 if action.partition(':')[0] not in ('keep', 'drop', 'pseudo', 'text'))
    if bad:
        print('unknown action for: ' + ', '.join(bad), file=sys.stderr)
        return 1
    run = Run(mapping, args.default, load_analyzer())
    results = []
    for name in args.files:
        path = pathlib.Path(name)
        writer = WRITERS.get(path.suffix.lower())
        if writer is None:
            print(f'{path.name}: format not supported', file=sys.stderr)
            return 1
        try:
            results.append((path, *writer(run, path, path.read_bytes())))
        except Unavailable as error:
            print(error, file=sys.stderr)
            return 2
        except Exception as error:
            print(f'{path.name}: {type(error).__name__}, nothing written', file=sys.stderr)
            return 1
    if run.missing:
        print('fields the map leaves out (map them, or pass --default):', file=sys.stderr)
        for field in sorted(run.missing):
            print(f'  {field}', file=sys.stderr)
        return 1
    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for path, data, suffix in results:
        target = out_dir / f'{path.stem}.anon{suffix}'
        target.write_bytes(data)
        print(f'{path.name} → {target}')
    report(run)
    return 0


if __name__ == '__main__':
    sys.exit(main())
