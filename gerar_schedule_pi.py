#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Gera/atualiza planing-interval-schedule.yaml com as datas (dias úteis) e
descrições de cada dia de sprint de UM Planning Interval (PI), seguindo o esquema SAFe.

🆕 Regra adicional de geração:
- Só GERA um novo PI se a **data atual** (America/Sao_Paulo) for **maior** que
  a **data de fim do último PI existente + 5 dias**.
- Se **não existir** nenhum PI (ou seja, o schedule está vazio/inexistente), **gera**.
- Caso a condição não seja atendida, o script **não gera** e informa o motivo.

Regras de início (quando for gerar):
- Se planing-interval-schedule.yaml existir:
    start = próximo dia útil após a última data do schedule
    SE existir PLANNING_INTERVAL_START_DATE e ela for posterior à última data,
    então start = data da variável (ajustada para próximo dia útil, se necessário)
- Se não existir:
    start = PLANNING_INTERVAL_START_DATE (obrigatória; ajusta para próximo dia útil)

Arquivos esperados (caminho padrão: diretório de execução):
- feriados.yaml
    Estrutura esperada:
    feriados:
      - data: 2025-11-15
        nome: "Republic Day"
      - data: 2025-12-25
        nome: "Christmas Day"
    (datas podem vir como string ISO ou já desserializadas)

- planing-interval.yaml
    Estrutura (exemplo real observado):
    pi:
      sprints: 5
      dias: 50
      tabela:
        - dia: 1
          sprint: 1
          dia_sprint: 1
          fase: "fase.planejamento"
          atividades: "atividades.kickoff"
          observacoes: "observacoes..."
          eventos_pi: "eventos_pi.pi_planning_day_1"
        - dia: 2
          ...

- planing-interval-schedule.yaml (saída/append)
    Formato produzido (lista YAML):
      - date: "2025-11-03"
        pi_day: 1
        sprint: 1
        day_in_sprint: 1
        descricao: "fase.planejamento | atividades.kickoff | eventos_pi.pi_planning_day_1"
        meta:
          fase: ...
          atividades: ...
          observacoes: ...
          eventos_pi: ...
"""

import os
import sys
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from typing import List, Dict, Any, Optional, Set

import yaml

try:
    # Python 3.9+: timezone local para America/Sao_Paulo
    from zoneinfo import ZoneInfo  # type: ignore
    _ZONEINFO_AVAILABLE = True
except Exception:
    _ZONEINFO_AVAILABLE = False

# --------- CONFIG BÁSICA (ajuste caminhos se quiser usar fora do diretório atual) ----------
ARQ_FERIADOS = Path("feriados.yaml")
ARQ_PI       = Path("planing-interval.yaml")
ARQ_SCHEDULE = Path("planing-interval-schedule.yaml")

ENV_START = "PLANNING_INTERVAL_START_DATE"  # valor ISO: YYYY-MM-DD

# ------------------------------------------------------------------------------------------

def hoje_sao_paulo() -> date:
    """Data de hoje na timezone America/Sao_Paulo (fallback para local se zoneinfo indisponível)."""
    if _ZONEINFO_AVAILABLE:
        tz = ZoneInfo("America/Sao_Paulo")
        return datetime.now(tz).date()
    return date.today()

def ler_yaml(caminho: Path) -> Any:
    with caminho.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def salvar_yaml(caminho: Path, conteudo: Any) -> None:
    with caminho.open("w", encoding="utf-8") as f:
        yaml.safe_dump(conteudo, f, allow_unicode=True, sort_keys=False)

def parse_data(val) -> date:
    """Aceita datetime.date, datetime, string ISO ('YYYY-MM-DD')."""
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, str):
        return date.fromisoformat(val.strip())
    raise ValueError(f"Formato de data não suportado: {val!r}")

def carregar_feriados(caminho: Path) -> Dict[date, str]:
    """Retorna dict {data: nome} para feriados."""
    dados = ler_yaml(caminho)
    feriados = {}
    if not dados or "feriados" not in dados:
        return feriados
    for item in dados["feriados"]:
        d = parse_data(item.get("data"))
        nome = item.get("nome", "")
        feriados[d] = nome
    return feriados

def eh_dia_util(d: date, feriados: Set[date]) -> bool:
    return d.weekday() < 5 and d not in feriados  # 0=segunda ... 6=domingo

def proximo_dia_util(d: date, feriados: Set[date]) -> date:
    """Se d já for útil, retorna d; caso contrário, avança até o próximo útil."""
    atual = d
    while not eh_dia_util(atual, feriados):
        atual += timedelta(days=1)
    return atual

def carregar_schedule(caminho: Path) -> List[Dict[str, Any]]:
    if not caminho.exists():
        return []
    dados = ler_yaml(caminho)
    if dados is None:
        return []
    if isinstance(dados, dict) and "schedule" in dados:
        # Suporta formato dict com chave "schedule"
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

def escolher_data_inicio(ult_data: Optional[date], env_str: Optional[str], feriados_set: Set[date]) -> date:
    """
    - Se schedule existe (ult_data != None):
        start = próximo dia útil após ult_data
        se env_str existir e for > ult_data => start = env_str (ajustada p/ útil)
    - Se schedule não existe:
        start = env_str (obrigatória; ajusta para próximo dia útil)
    """
    if ult_data is not None:
        start = proximo_dia_util(ult_data + timedelta(days=1), feriados_set)
        if env_str:
            env_dt = proximo_dia_util(parse_data(env_str), feriados_set)
            if env_dt > ult_data:
                start = env_dt
        return start

    # schedule não existe
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
    """
    Mapeia a sequência dos 'dias' do PI (na ordem do campo 'dia') para dias úteis consecutivos
    a partir de 'start'. Pula feriados e fins de semana.
    """
    # Ordena por 'dia' para garantir sequência, caso o YAML não esteja ordenado.
    tabela = sorted(pi_tabela, key=lambda x: int(x.get("dia", 0)))

    saida = []
    data_corrente = start

    for item in tabela:
        # Garante que data_corrente seja útil
        if not eh_dia_util(data_corrente, feriados_set):
            data_corrente = proximo_dia_util(data_corrente, feriados_set)

        registro = {
            "date": data_corrente.isoformat(),
            "pi_day": int(item.get("dia")),
            "sprint": int(item.get("sprint")),
            "day_in_sprint": int(item.get("dia_sprint")),
            "descricao": montar_descricao(item),
            "meta": {
                k: v for k, v in item.items()
                if k not in {"dia", "sprint", "dia_sprint"}
            }
        }
        saida.append(registro)

        # Avança 1 dia de calendário; o loop ajusta para útil no próximo ciclo
        data_corrente += timedelta(days=1)

    return saida

def deve_gerar_novo_pi(schedule: List[Dict[str, Any]]) -> bool:
    """
    Regra:
    - Se NÃO existe nenhum PI (schedule vazio) => True
    - Senão, pega a DATA MÁXIMA no schedule (fim do último PI) e só gera se
      hoje > (data_fim_ultimo_pi + 5 dias)
    """
    if not schedule:
        return True

    ultima = ultima_data_no_schedule(schedule)
    if not ultima:
        # Se por algum motivo não conseguimos determinar, seja permissivo: considera que pode gerar.
        return True

    limite = ultima + timedelta(days=5)
    hoje = hoje_sao_paulo()
    return hoje > limite

def main() -> None:
    # --- Carrega feriados ---
    if not ARQ_FERIADOS.exists():
        print(f"ERRO: não encontrei {ARQ_FERIADOS}", file=sys.stderr)
        sys.exit(1)
    mapa_feriados = carregar_feriados(ARQ_FERIADOS)
    feriados_set = set(mapa_feriados.keys())

    # --- Carrega PI (esquema SAFe do intervalo) ---
    if not ARQ_PI.exists():
        print(f"ERRO: não encontrei {ARQ_PI}", file=sys.stderr)
        sys.exit(1)
    pi_yaml = ler_yaml(ARQ_PI)
    try:
        pi_tabela = list(pi_yaml["pi"]["tabela"])
        if not pi_tabela:
            raise ValueError("pi.tabela está vazio.")
    except Exception as e:
        print("ERRO: Estrutura de planing-interval.yaml inesperada. "
              "Esperado chave 'pi.tabela' com lista de dias.", file=sys.stderr)
        raise

    # --- Carrega (ou não) schedule existente ---
    schedule = carregar_schedule(ARQ_SCHEDULE)

    # --- Verifica regra de só gerar após fim do último PI + 5 dias (ou se não existe nenhum PI) ---
    if not deve_gerar_novo_pi(schedule):
        ultima = ultima_data_no_schedule(schedule)
        limite = ultima + timedelta(days=5) if ultima else None
        hoje = hoje_sao_paulo()
        print("⚠️ Nenhum PI gerado.")
        if ultima:
            print(f"   Hoje: {hoje.isoformat()} | Fim do último PI: {ultima.isoformat()} | "
                  f"Permitido gerar após: {(limite + timedelta(days=0)).isoformat()} (fim + 5 dias)")
        else:
            print("   (Schedule existente porém sem data final identificável.)")
        sys.exit(0)

    # --- Resolve data inicial conforme regras (agora que sabemos que devemos gerar) ---
    ult_data = ultima_data_no_schedule(schedule)
    env_str = os.environ.get(ENV_START)
    start = escolher_data_inicio(ult_data, env_str, feriados_set)

    # --- Gera UM novo PI e acrescenta ao schedule ---
    novo_pi = gerar_um_pi(pi_tabela, start, feriados_set)
    schedule_atualizado = schedule + novo_pi

    # Salva como LISTA pura (simples e compatível).
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
