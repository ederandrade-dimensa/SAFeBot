#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Atualiza planing-interval-schedule.yaml de acordo com:
- "Reflow": mantém passado (< hoje) e recalcula a partir de hoje.
- 5 dias antes do fim do PI atual, pré-gera o próximo PI.

Continua respeitando:
- feriados.yaml (chave 'feriados': [{data, nome}])
- skip-dates.txt (datas ISO por linha, para pular dias específicos)
- planing-interval.yaml (tabela do PI com chaves: dia, sprint, dia_sprint)
"""

import os
import sys
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import List, Dict, Any, Optional, Set

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

ARQ_SKIP     = Path("skip-dates.txt")
ENV_START    = "PLANNING_INTERVAL_START_DATE"  # ainda suportado se não existir schedule
ENV_SKIP_EMENDAS = os.environ.get("SKIP_EMENDAS", "").strip().lower() in {"1", "true", "on", "yes", "y"}
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

def carregar_skip_dates(caminho: Path) -> Set[date]:
    datas: Set[date] = set()
    if not caminho.exists():
        return datas
    with caminho.open("r", encoding="utf-8") as f:
        for i, linha in enumerate(f, 1):
            s = linha.strip()
            if not s or s.startswith("#"):
                continue
            try:
                datas.add(date.fromisoformat(s))
            except Exception:
                print(f"⚠️ Linha {i} de {caminho} ignorada (esperado ISO YYYY-MM-DD): {s!r}", file=sys.stderr)
    return datas

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
    raise ValueError("Formato de planing-interval-schedule.yaml inesperado (lista ou dict{'schedule': [...]}).")

def data_do_item(o: Dict[str, Any]) -> Optional[date]:
    try:
        if "date" in o:
            return parse_data(o["date"])
    except Exception:
        pass
    return None

def split_schedule_por_data(schedule: List[Dict[str, Any]], pivot: date):
    """Retorna (passado, futuro) onde passado = datas < pivot; futuro = datas >= pivot"""
    passado, futuro = [], []
    for item in schedule:
        d = data_do_item(item)
        if d is None:
            passado.append(item)  # conserva itens sem data
            continue
        (passado if d < pivot else futuro).append(item)
    return passado, futuro
# ------------------------------------------------------------

# --------- Detecção robusta da tabela do PI -----------------
_REQUIRED_FIELDS = {"dia", "sprint", "dia_sprint"}

def _parece_item_pi(o: Any) -> bool:
    return isinstance(o, dict) and _REQUIRED_FIELDS.issubset(set(map(str, o.keys())))

def _extrair_lista_se_for_tabela(obj: Any) -> Optional[List[Dict[str, Any]]]:
    if isinstance(obj, list) and all(isinstance(x, dict) for x in obj):
        if not obj:
            return obj
        if all(_parece_item_pi(x) for x in obj):
            return obj
    return None

def _buscar_tabela_recursivo(obj: Any) -> Optional[List[Dict[str, Any]]]:
    cand = _extrair_lista_se_for_tabela(obj)
    if cand is not None:
        return cand
    if isinstance(obj, dict):
        for _, v in obj.items():
            achado = _extrair_lista_se_for_tabela(v)
            if achado is not None:
                return achado
        for _, v in obj.items():
            achado = _buscar_tabela_recursivo(v)
            if achado is not None:
                return achado
    if isinstance(obj, list):
        for item in obj:
            achado = _buscar_tabela_recursivo(item)
            if achado is not None:
                return achado
    return None

def carregar_pi_tabela(caminho: Path) -> List[Dict[str, Any]]:
    dados = ler_yaml(caminho)
    if isinstance(dados, dict) and "pi" in dados and isinstance(dados["pi"], dict) and "tabela" in dados["pi"]:
        tbl = _extrair_lista_se_for_tabela(dados["pi"]["tabela"])
        if tbl is not None:
            return list(tbl)
    if isinstance(dados, dict) and "tabela" in dados:
        tbl = _extrair_lista_se_for_tabela(dados["tabela"])
        if tbl is not None:
            return list(tbl)
    if isinstance(dados, list):
        tbl = _extrair_lista_se_for_tabela(dados)
        if tbl is not None:
            return list(tbl)
    tbl = _buscar_tabela_recursivo(dados)
    if tbl is not None:
        return list(tbl)
    msg = [
        "Estrutura de planing-interval.yaml inesperada.",
        "Procura-se por lista de itens com chaves: 'dia', 'sprint', 'dia_sprint'.",
        f"Topo: {sorted(list(dados.keys())) if isinstance(dados, dict) else type(dados).__name__}"
    ]
    raise ValueError("\n".join(msg))
# ------------------------------------------------------------

# ----------------- Funções auxiliares novas -----------------
def escolher_start_para_reflow(hoje: date, feriados_set: Set[date]) -> date:
    return proximo_dia_util(hoje, feriados_set)

def montar_descricao(item: Dict[str, Any]) -> str:
    partes = []
    for chave in ("fase", "atividades", "observacoes", "eventos_pi"):
        v = item.get(chave)
        if v:
            partes.append(str(v))
    return " | ".join(partes) if partes else ""

def gerar_um_pi(
        pi_tabela: List[Dict[str, Any]],
        start: date,
        feriados_set: Set[date],
        pi_number: Optional[int] = None,
) -> List[Dict[str, Any]]:
    tabela = sorted(pi_tabela, key=lambda x: int(x.get("dia", 0)))
    saida = []
    data_corrente = start
    for item in tabela:
        if not eh_dia_util(data_corrente, feriados_set):
            data_corrente = proximo_dia_util(data_corrente, feriados_set)

        registro: Dict[str, Any] = {
            "date": data_corrente.isoformat(),
            "pi_day": int(item.get("dia")),
            "sprint": int(item.get("sprint")),
            "day_in_sprint": int(item.get("dia_sprint")),
            "descricao": montar_descricao(item),
            # mantém os extras em meta, mas sem duplicar 'cor'
            "meta": {k: v for k, v in item.items() if k not in {"dia", "sprint", "dia_sprint", "cor"}},
        }

        if "cor" in item and item["cor"] is not None:
            registro["cor"] = item["cor"]

        if pi_number is not None:
            registro["pi"] = pi_number

        saida.append(registro)
        data_corrente += timedelta(days=1)
    return saida


def calcular_emendas(feriados: Set[date]) -> Set[date]:
    """
    Regras:
      - Se o feriado cai na terça (weekday == 1), pula a segunda (d - 1).
      - Se o feriado cai na quinta (weekday == 3), pula a sexta (d + 1).
    Observações:
      - Só adiciona se esses dias forem dias de semana.
      - Não duplica se já for feriado.
    """
    emendas: Set[date] = set()
    for d in feriados:
        if d.weekday() == 1:  # terça
            segunda = d - timedelta(days=1)
            if 0 <= segunda.weekday() <= 4 and segunda not in feriados:
                emendas.add(segunda)
        elif d.weekday() == 3:  # quinta
            sexta = d + timedelta(days=1)
            if 0 <= sexta.weekday() <= 4 and sexta not in feriados:
                emendas.add(sexta)
    return emendas
# ------------------------------------------------------------

def primeira_data_no_schedule(schedule: List[Dict[str, Any]]) -> Optional[date]:
    if not schedule:
        return None
    try:
        return min(parse_data(item["date"]) for item in schedule if "date" in item)
    except Exception:
        return None

def ultima_data_no_schedule(schedule: List[Dict[str, Any]]) -> Optional[date]:
    if not schedule:
        return None
    try:
        return max(parse_data(item["date"]) for item in schedule if "date" in item)
    except Exception:
        return None

def max_pi_number(schedule: List[Dict[str, Any]]) -> int:
    max_pi = 0
    for item in schedule:
        pi = item.get("pi")
        if isinstance(pi, int) and pi > max_pi:
            max_pi = pi
    return max_pi

# ----------------- Lógica principal -------------------------
def main() -> None:
    # --- entradas obrigatórias ---
    if not ARQ_FERIADOS.exists():
        print(f"ERRO: não encontrei {ARQ_FERIADOS}", file=sys.stderr)
        sys.exit(1)
    if not ARQ_PI.exists():
        print(f"ERRO: não encontrei {ARQ_PI}", file=sys.stderr)
        sys.exit(1)

    # --- feriados + skips ---
    mapa_feriados = carregar_feriados(ARQ_FERIADOS)
    feriados_set = set(mapa_feriados.keys())
    skip_set = carregar_skip_dates(ARQ_SKIP)

    emendas_set: Set[date] = set()
    if ENV_SKIP_EMENDAS:
        emendas_set = calcular_emendas(feriados_set)
        if emendas_set:
            print(f"Emendas habilitadas: {len(emendas_set)} dia(s) incluído(s) como skip devido a feriados em 3ª/5ª.")

    if skip_set:
        print(f"Skip dates: {len(skip_set)} data(s) será(ão) pulada(s) ({ARQ_SKIP}).")

    feriados_ou_skips = feriados_set | emendas_set | skip_set

    # --- tabela do PI ---
    try:
        pi_tabela = carregar_pi_tabela(ARQ_PI)
        if not pi_tabela:
            raise ValueError("Lista de dias do PI está vazia.")
        print(f"PI detectado com {len(pi_tabela)} linhas.")
    except Exception as e:
        print("ERRO ao interpretar planing-interval.yaml:\n" + str(e), file=sys.stderr)
        sys.exit(1)

    # --- schedule existente ---
    schedule = carregar_schedule(ARQ_SCHEDULE)
    hoje = hoje_sao_paulo()
    env_str = os.environ.get(ENV_START)

    # Se não existe schedule ainda, usa ENV_START como bootstrap
    if not schedule:
        if not env_str:
            print(f"ERRO: {ARQ_SCHEDULE} não existe e {ENV_START} não foi definida.", file=sys.stderr)
            sys.exit(1)

        start_boot = proximo_dia_util(parse_data(env_str), feriados_ou_skips)

        # Primeiro PI sempre como pi = 1
        atual = gerar_um_pi(pi_tabela, start_boot, feriados_ou_skips, pi_number=1)
        salvar_yaml(ARQ_SCHEDULE, atual)
        fim = parse_data(atual[-1]["date"])
        print(f"✅ Schedule criado do zero: {len(atual)} dias úteis ({atual[0]['date']} → {atual[-1]['date']}).")

        # Se já estiver a ≤5 dias do fim, já emenda o próximo PI (pi = 2)
        faltam_dias = (fim - hoje).days
        if faltam_dias <= 5:
            prox_start = proximo_dia_util(fim + timedelta(days=1), feriados_ou_skips)
            prox = gerar_um_pi(pi_tabela, prox_start, feriados_ou_skips, pi_number=2)
            salvar_yaml(ARQ_SCHEDULE, atual + prox)
            print(f"👉 Janela ≤5 dias: próximo PI também gerado ({prox[0]['date']} → {prox[-1]['date']}).")
        sys.exit(0)

    # A partir daqui: já existe schedule
    primeira = primeira_data_no_schedule(schedule)
    ultimo = ultima_data_no_schedule(schedule)

    # Tenta interpretar ENV_START (se existir)
    env_data = None
    if env_str:
        try:
            env_data_bruta = parse_data(env_str)
            env_data = proximo_dia_util(env_data_bruta, feriados_ou_skips)
        except Exception as e:
            print(f"⚠️ {ENV_START} ignorada (valor inválido: {env_str!r}): {e}", file=sys.stderr)
            env_data = None

    # Caso especial: PLANNING_INTERVAL_START_DATE depois do último dia → anexar novo PI e sair
    if env_data and ultimo and env_data > ultimo:
        proximo_pi_number = max_pi_number(schedule) + 1 or 1
        novo_pi = gerar_um_pi(pi_tabela, env_data, feriados_ou_skips, pi_number=proximo_pi_number)
        schedule_atualizado = schedule + novo_pi
        salvar_yaml(ARQ_SCHEDULE, schedule_atualizado)
        print(
            f"✅ Novo PI #{proximo_pi_number} anexado: {len(novo_pi)} dias úteis "
            f"({novo_pi[0]['date']} → {novo_pi[-1]['date']})."
        )
        sys.exit(0)

    # NOVO COMPORTAMENTO:
    # Se todo o schedule está no futuro em relação a hoje, não faz reflow, não recria nada.
    if primeira and hoje < primeira:
        print(
            f"ℹ️ Hoje ({hoje}) é antes da primeira data agendada ({primeira}). "
            f"Nenhuma alteração feita no schedule."
        )
        sys.exit(0)

    # --- escolha do pivot e do start do reflow ---
    pivot = hoje
    start = escolher_start_para_reflow(hoje, feriados_ou_skips)

    # --- REFLOW: manter passado (< pivot), descartar futuro (>= pivot) e recalcular a partir de start ---
    passado, futuro = split_schedule_por_data(schedule, pivot)

    # Descobre qual PI estou recalculando
    if passado:
        pi_atual_number = max_pi_number(passado)
        if pi_atual_number == 0 and futuro:
            pi_atual_number = futuro[0].get("pi", 1)
        elif pi_atual_number == 0:
            pi_atual_number = 1
    elif futuro:
        pi_atual_number = futuro[0].get("pi", 1)
    else:
        pi_atual_number = 1

    pi_atual = gerar_um_pi(pi_tabela, start, feriados_ou_skips, pi_number=pi_atual_number)
    fim_atual = parse_data(pi_atual[-1]["date"])

    # preserva apenas o que vier *depois* do fim recalculado
    futuro_apos = []
    for item in futuro:
        d = data_do_item(item)
        if d is None:
            continue
        if d > fim_atual:
            futuro_apos.append(item)

    schedule_atualizado = passado + pi_atual + futuro_apos

    # --- checar janela de 5 dias para pré-gerar próximo PI ---
    faltam_dias = (fim_atual - hoje).days
    ja_existe_proximo = len(futuro_apos) > 0

    if faltam_dias <= 5 and not ja_existe_proximo:
        prox_start = proximo_dia_util(fim_atual + timedelta(days=1), feriados_ou_skips)
        proximo_pi_number = max_pi_number(schedule_atualizado) + 1 or 1
        prox_pi = gerar_um_pi(pi_tabela, prox_start, feriados_ou_skips, pi_number=proximo_pi_number)
        schedule_atualizado += prox_pi
        print(
            f"⏩ A {faltam_dias} dia(s) do fim: próximo PI #{proximo_pi_number} pré-gerado "
            f"({prox_pi[0]['date']} → {prox_pi[-1]['date']})."
        )
    elif faltam_dias <= 5 and ja_existe_proximo:
        print(f"ℹ️ A {faltam_dias} dia(s) do fim: já havia PI futuro no schedule; não gerei outro.")

    salvar_yaml(ARQ_SCHEDULE, schedule_atualizado)

    print(f"✅ Reflow aplicado. Mantidos {len(passado)} itens do passado.")
    print(f"   PI atual (#{pi_atual_number}): {len(pi_atual)} dias úteis ({pi_atual[0]['date']} → {pi_atual[-1]['date']})")
    if faltam_dias <= 5:
        if ja_existe_proximo:
            print("   Próximo PI já existia e foi preservado.")
        else:
            print("   Próximo PI foi gerado agora.")
    else:
        print(f"   Ainda faltam {faltam_dias} dia(s) corridos para o fim do PI atual; "
              f"próximo PI será gerado automaticamente quando atingir ≤ 5.")


if __name__ == "__main__":
    main()
