#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Gera 'feriados.yaml' a partir de um ICS, excluindo eventos cujo SUMMARY termina com '(Government Holiday)'.

Parâmetros via variáveis de ambiente:
  - ICS_URL: URL do arquivo ICS (ex: https://www.officeholidays.com/ics-clean/brazil/sao-paulo)
  - OUTPUT_YAML: nome do arquivo YAML de saída (default: feriados.yaml)
  - MONTHS_AHEAD: meses a partir de hoje (default: 12)
  - START_DATE: data inicial fixa no formato YYYY-MM-DD (opcional)
  - END_DATE: data final fixa no formato YYYY-MM-DD (opcional)

Exemplo de uso:
  $ export ICS_URL="https://www.officeholidays.com/ics-clean/brazil/sao-paulo"
  $ export MONTHS_AHEAD=6
  $ python3 gerar_feriados.py
"""

import os
from urllib.request import urlopen
from datetime import datetime, date, timedelta
from typing import List, Dict

# === Lê variáveis de ambiente ===
ICS_URL = os.getenv("ICS_URL", "https://www.officeholidays.com/ics-clean/brazil/sao-paulo")
OUTPUT_YAML = os.getenv("OUTPUT_YAML", "feriados.yaml")
MONTHS_AHEAD = int(os.getenv("MONTHS_AHEAD", "12"))

# Datas opcionais fixas
START_DATE_ENV = os.getenv("START_DATE")
END_DATE_ENV = os.getenv("END_DATE")

# === Define intervalo ===
if START_DATE_ENV and END_DATE_ENV:
    START_DATE = datetime.strptime(START_DATE_ENV, "%Y-%m-%d").date()
    END_DATE = datetime.strptime(END_DATE_ENV, "%Y-%m-%d").date()
else:
    today = date.today()
    START_DATE = today
    END_DATE = today + timedelta(days=30 * MONTHS_AHEAD)

def _unfold_ical_lines(raw_text: str) -> List[str]:
    lines = raw_text.splitlines()
    unfolded = []
    for ln in lines:
        if ln.startswith(" "):  # continuação da linha anterior
            if unfolded:
                unfolded[-1] += ln[1:]
        else:
            unfolded.append(ln)
    return unfolded

def _parse_ical_datetime(value: str) -> date:
    fmts = ["%Y%m%d", "%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S"]
    for fmt in fmts:
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Formato de data não suportado: {value}")

def _extract_events(ics_text: str) -> List[Dict[str, str]]:
    lines = _unfold_ical_lines(ics_text)
    events = []
    in_event = False
    curr = {}

    for ln in lines:
        if ln == "BEGIN:VEVENT":
            in_event = True
            curr = {}
        elif ln == "END:VEVENT":
            if in_event and "summary" in curr and "date" in curr:
                events.append(curr)
            in_event = False
            curr = {}
        elif in_event:
            if ln.startswith("SUMMARY"):
                value = ln.split(":", 1)[1].strip() if ":" in ln else ""
                curr["summary"] = value
            elif ln.startswith("DTSTART"):
                value = ln.split(":", 1)[1].strip() if ":" in ln else ""
                try:
                    curr["date"] = _parse_ical_datetime(value)
                except ValueError:
                    pass
    return events

def _filter_events(events: List[Dict[str, str]]) -> List[Dict[str, str]]:
    filtered = []
    for e in events:
        summary = e.get("summary", "").strip()
        d = e.get("date")
        if not summary or not d:
            continue
        if summary.endswith("(Government Holiday)"):
            continue
        if START_DATE <= d < END_DATE:
            filtered.append({"summary": summary, "date": d})
    filtered.sort(key=lambda x: x["date"])
    return filtered

def _write_yaml(events: List[Dict[str, str]], filepath: str) -> None:
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("feriados:\n")
        for e in events:
            f.write(f"  - data: {e['date'].isoformat()}\n")
            name = e["summary"]
            if any(c in name for c in [":", "-", "#", "{", "}", "[", "]", ",", "&", "*", "!", "|", ">", "'", "\"", "%", "@", "`"]):
                name = name.replace("\"", "\\\"")
                f.write(f"    nome: \"{name}\"\n")
            else:
                f.write(f"    nome: {name}\n")

def main():
    print(f"Baixando ICS de {ICS_URL} ...")
    with urlopen(ICS_URL) as resp:
        ics_text = resp.read().decode("utf-8", errors="replace")

    events = _extract_events(ics_text)
    events = _filter_events(events)
    _write_yaml(events, OUTPUT_YAML)

    print(f"✅ Gerado '{OUTPUT_YAML}' com {len(events)} feriado(s) entre {START_DATE} e {END_DATE - timedelta(days=1)}.")

if __name__ == "__main__":
    main()
