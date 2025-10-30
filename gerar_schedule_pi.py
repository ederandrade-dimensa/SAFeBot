#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Gera/atualiza planing-interval-schedule.yaml com as datas (dias úteis) e
descrições de cada dia de sprint de UM Planning Interval (PI), seguindo o esquema SAFe.

Regras de geração:
- Só GERA um novo PI se a data de hoje (America/Sao_Paulo) for > (fim do último PI + 5 dias).
- Se NÃO existir nenhum PI no schedule, gera.
- Caso a condição não seja atendida, não gera e sai com código 0.

Regras de início (quando for gerar):
- Se planing-interval-schedule.yaml existir:
    start = próximo dia útil após a última data do schedule
    SE existir PLANNING_INTERVAL_START_DATE e ela for posterior à última data,
    então start = data da variável (ajustada para próximo dia útil)
- Se não existir:
    start = PLANNING_INTERVAL_START_DATE (obrigatória; ajusta para próximo dia útil)

Variáveis de ambiente úteis:
- PLANNING_INTERVAL_START_DATE   -> data ISO (YYYY-MM-DD) usada quando não existe schedule
- PLANING_INTERVAL_FILE          -> caminho do YAML do PI (default: planing-interval.yaml)
- PLANING_INTERVAL_SCHEDULE_FILE -> caminho do schedule (default: planing-interval-schedule.yaml)
- FERIADOS_FILE                  -> caminho do YAML de feriados (default: feriados.yaml)
"""

import os
import sys
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import List, Dict, Any, Optional, Set, Iterable

import yaml

try:
    from zoneinfo import ZoneInfo
    _ZONEINFO_AVAILABLE = True
except Exception:
    _ZONEINFO_AVAILABLE = False

# --------- CONFIG (pode sobrescrever por env vars) ----------
ARQ_FERIADOS = Path(os.environ.get("FERIADOS_FILE", "feriados.yaml"))
ARQ_PI       = Path(os.environ.get("PLANING_INTERVAL_FILE", "planing-interval.yaml"))
ARQ_SCHEDULE = Path(os.environ.get("PLANING_INTERVAL_SCHEDULE_FILE", "planing-interval-schedule.yaml"))

ENV_START = "PLANNING_INTERVAL_START_DATE"  # valor ISO: YYYY-MM-DD
# ------------------------------------------------------------

# ----------------- Utilidades de data -----------------------
def hoje_sao_paulo() -> date:
    if _ZONEINFO_AVAILABLE:
        return datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    return date.today()

def parse_data(val) -> date:
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, str):
        return date.fromisoformat(val.strip())
    raise ValueError(f"Formato de data não suportado: {val!r}")

def eh_dia_util(d: date, feriados: Set[date]) -> bool:
    return d.weekday() < 5 and d not in feriados

def proximo_dia_util(d: date, feriados: Set[date]) -> date:
    atual = d
    while not eh_dia_util(atual, feriados):
        atual += timedelta(days=1)
    return atual
# ------------------------------------------------------------

# ----------------- IO de YAML -------------------------------
def ler_yaml(caminho: Path) -> Any:
    with caminho.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def salvar_yaml(caminho: Path, conteudo: Any) -> None:
    with caminho.open("w", encoding="utf-8") as f:
        yaml.safe_dump(conteudo, f, allow_unicode=True, sort_keys=False)

def carregar_feriados(caminho: Path) -> Dict[date, str]:
    dados = ler_yaml(caminho)
    feriados = {}
    if not dados or "feriados" not in dados:
        return feriados
    for item in dados["feriados"]:
        d = parse_data(item.get("data"))
        nome = item.get("nome", "")
        feriados[d] = nome
    return feriados

def carregar_schedule(caminho: Path) -> List[Dict[str, Any]]:
    if not caminho.exists():
        return []
    dados = ler_yaml(caminho)
    if dados is None:
        return []
    if isinstance(dados, dict) and "schedule" in dados:
        return list(dados.get("schedule") or [])
    if isinstance(dados, list):
        return dados
    raise ValueError("Formato de planing-interval-schedule.yaml inesperado (esperado lista ou dict{'schedule': [...]}).")

def ultima_data_no_schedule(schedule: List[Dict[str, Any]]) -> Optional[date]:
    if not schedule:
        return None
    try:
        return max(parse_data(item["date"]) for item in schedule if "date" in item)
    except Exception:
        return None
# ------------------------------------------------------------

# --------- Detecção robusta da tabela do PI -----------------
_REQUIRED_FIELDS = {"dia", "sprint", "dia_sprint"}

def _parece_item_pi(o: Any) -> bool:
    return isinstance(o, dict) and _REQUIRED_FIELDS.issubset(set(map(str, o.keys())))

def _extrair_lista_se_for_tabela(obj: Any) -> Optional[List[Dict[str, Any]]]:
    if isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj):
        # Aceita lista vazia também, mas aqui exigimos que tenha os campos esperados quando houver itens
        if not obj:  # lista vazia (cairia no if anterior), mas mantemos para clareza
            return obj
        if all(_parece_item_pi(x) for x in obj):
            return obj
    return None

def _buscar_tabela_recursivo(obj: Any, trilha: str = "root") -> Optional[List[Dict[str, Any]]]:
    """
    Varre recursivamente o objeto em busca de uma lista de dicts contendo
    ao menos os campos essenciais ('dia', 'sprint', 'dia_sprint').
    """
    cand = _extrair_lista_se_for_tabela(obj)
    if cand is not None:
        return cand

    if isinstance(obj, dict):
        for k, v in obj.items():
            achado = _extrair_lista_se_for_tabela(v)
            if achado is not None:
                return achado
        # busca mais profunda
        for k, v in obj.items():
            achado = _buscar_tabela_recursivo(v, f"{trilha}.{k}")
            if achado is not None:
                return achado

    if isinstance(obj, list):
        for idx, item in enumerate(obj):
            achado = _buscar_tabela_recursivo(item, f"{trilha}[{idx}]")
            if achado is not None:
                return achado

    return None

def carregar_pi_tabela(caminho: Path) -> List[Dict[str, Any]]:
    """
    Tenta vários formatos:
      1) raiz['pi']['tabela']
      2) raiz['tabela']
      3) raiz é uma lista de itens com (dia, sprint, dia_sprint)
      4) busca recursiva por qualquer lista de itens com esses campos
    """
    dados = ler_yaml(caminho)

    # 1) pi.tabela
    if isinstance(dados, dict) and "pi" in dados and isinstance(dados["pi"], dict) and "tabela" in dados["pi"]:
        tbl = _extrair_lista_se_for_tabela(dados["pi"]["tabela"])
        if tbl is not None:
            return list(tbl)

    # 2) tabela na raiz
    if isinstance(dados, dict) and "tabela" in dados:
        tbl = _extrair_lista_se_for_tabela(dados["tabela"])
        if tbl is not None:
            return list(tbl)

    # 3) raiz é lista
    if isinstance(dados, list):
        tbl = _extrair_lista_se_for_tabela(dados)
        if tbl is not None:
            return list(tbl)

    # 4) busca recursiva
    tbl = _buscar_tabela_recursivo(dados)
    if tbl is not None:
        return list(tbl)

    # Erro com diagnóstico útil
    msg = [
        "Estrutura de planing-interval.yaml inesperada.",
        "O script procura por uma lista de itens contendo as chaves: 'dia', 'sprint', 'dia_sprint'.",
        f"Chaves de topo encontradas: {sorted(list(dados.keys())) if isinstance(dados, dict) else type(dados).__name__}"
    ]
    raise ValueError("\n".join(msg))
# ------------------------------------------------------------

# ----------------- Lógica de negócio ------------------------
def escolher_data_inicio(ult_data: Optional[date], env_str: Optional[str], feriados_set: Set[date]) -> date:
    if ult_data is not None:
        start = proximo_dia_util(ult_data + timedelta(days=1), feriados_set)
        if env_str:
            env_dt = proximo_dia_util(parse_data(env_str), feriados_set)
            if env_dt > ult_data:
                start = env_dt
        return start

    if not env_str:
        raise RuntimeError(
            f"Variável de ambiente {ENV_START} é obrigatória quando o schedule ainda não existe."
        )
    return proximo_dia_util(parse_data(env_str), feriados_set)

def montar_descricao(item: Dict[str, Any]) -> str:
    partes = []
    for chave in ("fase", "atividades", "observacoes", "eventos_pi"):
        v = item.get(chave)
        if v:
            partes.append(str(v))
    return " | ".join(partes) if partes else ""

def gerar_um_pi(pi_tabela: List[Dict[str, Any]], start: date, feriados_set: Set[date]) -> List[Dict[str, Any]]:
    tabela = sorted(pi_tabela, key=lambda x: int(x.get("dia", 0)))

    saida = []
    data_corrente = start

    for item in tabela:
        if not eh_dia_util(data_corrente, feriados_set):
            data_corrente = proximo_dia_util(data_corrente, feriados_set)

        registro = {
            "date": data_corrente.isoformat(),
            "pi_day": int(item.get("dia")),
            "sprint": int(item.get("sprint")),
            "day_in_sprint": int(item.get("dia_sprint")),
            "descricao": montar_descricao(item),
            "meta": {k: v for k, v in item.items() if k not in {"dia", "sprint", "dia_sprint"}}
        }
        saida.append(registro)
        data_corrente += timedelta(days=1)

    return saida

def deve_gerar_novo_pi(schedule: List[Dict[str, Any]]) -> bool:
    if not schedule:
        return True
    ultima = ultima_data_no_schedule(schedule)
    if not ultima:
        return True
    limite = ultima + timedelta(days=5)
    return hoje_sao_paulo() > limite
# ------------------------------------------------------------

def main() -> None:
    # --- feriados ---
    if not ARQ_FERIADOS.exists():
        print(f"ERRO: não encontrei {ARQ_FERIADOS}", file=sys.stderr)
        sys.exit(1)
    mapa_feriados = carregar_feriados(ARQ_FERIADOS)
    feriados_set = set(mapa_feriados.keys())

    # --- PI ---
    if not ARQ_PI.exists():
        print(f"ERRO: não encontrei {ARQ_PI}", file=sys.stderr)
        sys.exit(1)

    try:
        pi_tabela = carregar_pi_tabela(ARQ_PI)
        if not pi_tabela:
            raise ValueError("Lista de dias do PI está vazia.")
        print(f"PI detectado com {len(pi_tabela)} linhas.")
    except Exception as e:
        print("ERRO ao interpretar planing-interval.yaml:\n" + str(e), file=sys.stderr)
        sys.exit(1)

    # --- schedule ---
    schedule = carregar_schedule(ARQ_SCHEDULE)

    # --- regra de janela de 5 dias ---
    if not deve_gerar_novo_pi(schedule):
        ultima = ultima_data_no_schedule(schedule)
        limite = ultima + timedelta(days=5) if ultima else None
        hoje = hoje_sao_paulo()
        print("⚠️ Nenhum PI gerado.")
        if ultima:
            print(f"   Hoje: {hoje.isoformat()} | Fim do último PI: {ultima.isoformat()} | "
                  f"Permitido gerar após: {limite.isoformat()}")
        else:
            print("   (Schedule existente porém sem data final identificável.)")
        sys.exit(0)

    # --- data de início ---
    ult_data = ultima_data_no_schedule(schedule)
    env_str = os.environ.get(ENV_START)
    try:
        start = escolher_data_inicio(ult_data, env_str, feriados_set)
    except Exception as e:
        print("ERRO ao determinar data inicial:\n" + str(e), file=sys.stderr)
        sys.exit(1)

    # --- gera 1 PI ---
    novo_pi = gerar_um_pi(pi_tabela, start, feriados_set)
    schedule_atualizado = schedule + novo_pi
    salvar_yaml(ARQ_SCHEDULE, schedule_atualizado)

    print(f"✅ Schedule atualizado em: {ARQ_SCHEDULE}")
    print(f"   Novo PI inserido: {len(novo_pi)} dias úteis "
          f"(de {novo_pi[0]['date']} a {novo_pi[-1]['date']})")
    if ult_data:
        print(f"   Fim do PI anterior era {ult_data.isoformat()} | "
              f"Geração permitida após {(ult_data + timedelta(days=5)).isoformat()} | "
              f"Hoje: {hoje_sao_paulo().isoformat()}")


if __name__ == "__main__":
    main()
