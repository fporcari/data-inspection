#!/usr/bin/env python3
"""End-to-end check on synthetic data: no value leaves the scripts, fakes stay valid.

Usage:
    python3 tests/check.py

Runs shape.py and anonymize.py twice: once with the Presidio venv when it exists,
once forced onto the calling Python without Presidio. Exit 0 means every check
passed; a failure names the check and never the leaked values.
"""
import csv
import json
import os
import pathlib
import random
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from common import cf_check, iban_valid, vat_check  # noqa: E402

NAMES = ['Giulia Bianchi', 'Marco Verdi', 'Anna Neri', 'Luca Galli', 'Sara Conti']
IBAN = 'IT60X0542811101000000123456'


def synthetic(folder):
    rnd = random.Random(7)
    people = []
    for _ in range(40):
        body = (''.join(rnd.choice('BCDFGHLMNPRSTVZ') for _ in range(6)) + f'{rnd.randint(40, 99)}'
                + rnd.choice('ABCDEHLMPRST') + f'{rnd.randint(1, 28):02d}' + 'H501')
        digits = ''.join(str(rnd.randint(0, 9)) for _ in range(10))
        people.append((body + cf_check(body), rnd.choice(NAMES), digits + vat_check(digits)))
    with open(folder / 'clients.csv', 'w', newline='') as handle:
        writer = csv.writer(handle, delimiter=';')
        writer.writerow(['id', 'cod_fisc', 'nominativo', 'piva', 'importo'])
        for i, (cf, name, vat) in enumerate(people):
            writer.writerow([i, cf, name, vat, f'{rnd.randint(10, 999)},50'])
    keyed = {cf: {'nome': name} for cf, name, _ in people}
    (folder / 'clients.json').write_text(json.dumps({'clienti': keyed}))
    cf, name, _ = people[0]
    first, last = name.split()
    (folder / 'invoice.xml').write_text(
        '<?xml version="1.0"?><p:FatturaElettronica xmlns:p="urn:x">'
        f'<CessionarioCommittente><CodiceFiscale>{cf}</CodiceFiscale><Anagrafica>'
        f'<Nome>{first}</Nome><Cognome>{last}</Cognome></Anagrafica></CessionarioCommittente>'
        f'<DettaglioPagamento><IBAN>{IBAN}</IBAN></DettaglioPagamento>'
        '<PrezzoTotale>100.00</PrezzoTotale></p:FatturaElettronica>')
    secrets = {IBAN} | {p[0] for p in people} | {p[2] for p in people} | set(NAMES)
    secrets |= {part for n in NAMES for part in n.split()}
    return people, secrets


def run(script, *args, env=None):
    result = subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args)],
                            capture_output=True, text=True, env={**os.environ, **(env or {})})
    return result.returncode, result.stdout + result.stderr


def assert_clean(label, text, secrets):
    leaked = sorted(s for s in secrets if s in text)
    assert not leaked, f'{label}: {len(leaked)} original values in the output'


def check(folder, env, label):
    people, secrets = synthetic(folder)
    for name in ('clients.csv', 'clients.json', 'invoice.xml'):
        code, out = run('shape.py', folder / name, env=env)
        assert code == 0, f'{label}: shape.py exit {code} on {name}'
        assert_clean(f'{label} shape {name}', out, secrets)
    mapping = {'cod_fisc': 'pseudo', 'nominativo': 'pseudo:PERSON', 'piva': 'pseudo',
               '.clienti{keys}': 'pseudo', '.clienti{*}.nome': 'pseudo:PERSON',
               '/FatturaElettronica/CessionarioCommittente/CodiceFiscale': 'pseudo',
               '/FatturaElettronica/CessionarioCommittente/Anagrafica/Nome': 'pseudo:PERSON',
               '/FatturaElettronica/CessionarioCommittente/Anagrafica/Cognome': 'pseudo:PERSON',
               '/FatturaElettronica/DettaglioPagamento/IBAN': 'pseudo'}
    (folder / 'map.json').write_text(json.dumps(mapping))
    files = [folder / 'invoice.xml', folder / 'clients.csv', folder / 'clients.json']
    code, out = run('anonymize.py', '--map', folder / 'map.json', '--out-dir', folder / 'partial', *files, env=env)
    assert code == 1 and not (folder / 'partial').exists(), f'{label}: an incomplete map must write nothing'
    code, out = run('anonymize.py', '--map', folder / 'map.json', '--out-dir', folder / 'anon',
                    '--default', 'keep', *files, env=env)
    assert code == 0, f'{label}: anonymize.py exit {code}'
    assert_clean(f'{label} anonymize stdout', out, secrets)
    copies = {p.name: p.read_text() for p in (folder / 'anon').iterdir()}
    assert_clean(f'{label} copies', ''.join(copies.values()), secrets)
    rows = list(csv.reader(copies['clients.anon.csv'].splitlines(), delimiter=';'))[1:]
    assert all(cf_check(r[1][:15]) == r[1][15] for r in rows), f'{label}: fake tax id invalid'
    assert all(vat_check(r[3][:10]) == r[3][10] for r in rows), f'{label}: fake VAT number invalid'
    root = ET.fromstring(copies['invoice.anon.xml'])
    fake_cf = root.find('.//CodiceFiscale').text
    assert fake_cf == rows[0][1] and fake_cf in json.loads(copies['clients.anon.json'])['clienti'], \
        f'{label}: the same tax id must get the same pseudonym in every file'
    assert iban_valid(root.find('.//IBAN').text), f'{label}: fake IBAN invalid'


def main():
    with tempfile.TemporaryDirectory() as tmp:
        check(pathlib.Path(tmp), None, 'default python (Presidio venv when present)')
    with tempfile.TemporaryDirectory() as tmp:
        folder = pathlib.Path(tmp)
        check(folder, {'DATA_INSPECTION_REEXEC': '1'}, 'without Presidio')
        (folder / 'text.json').write_text(json.dumps({'nominativo': 'text'}))
        code, _ = run('anonymize.py', '--map', folder / 'text.json', '--out-dir', folder / 't',
                      '--default', 'keep', folder / 'clients.csv', env={'DATA_INSPECTION_REEXEC': '1'})
        assert code == 2, 'text without Presidio must refuse with exit 2'


if __name__ == '__main__':
    main()
