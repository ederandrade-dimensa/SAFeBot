/***********************
 * CONFIG
 ***********************/
const CALENDAR_ID = 'c_674c89d4a397890885bc9b4cb938322737e4038cb61501be52bb918a38ccf568@group.calendar.google.com';
const URL_DICT = 'https://ederandrade-dimensa.github.io/SAFeBot/planing-interval.yaml';
const URL_SCHEDULE = 'https://ederandrade-dimensa.github.io/SAFeBot/planing-interval-schedule.yaml';
const JS_YAML_CDN = 'https://cdn.jsdelivr.net/npm/js-yaml@4.1.0/dist/js-yaml.min.js';

// Limpeza
const ENABLE_CLEANUP = true;  // se false, nunca apaga nada
const DRY_RUN = false;        // se true, só loga o que apagaria

// Rate limit / backoff
const SLEEP_MS_BETWEEN_CALLS = 100; // throttle leve
const MAX_RETRIES = 5;              // tentativas para 429/5xx
const BASE_BACKOFF_MS = 400;        // base do backoff exponencial

// NOVO: timezone para eventos com horário
const TIMEZONE = 'America/Sao_Paulo';

/***********************
 * ENTRADA
 ***********************/
function syncPlanningIntervalSchedule() {
  console.log('=== PI Sync: início ===');
  ensureJsYaml_();

  // 1) Baixa e parseia YAMLs
  const dictYamlText = fetchTextUtf8_(URL_DICT);
  const scheduleYamlText = fetchTextUtf8_(URL_SCHEDULE);

  const dictObj = globalThis.jsyaml.load(dictYamlText) || {};
  const scheduleList = globalThis.jsyaml.load(scheduleYamlText) || [];
  const translations = dictObj.translations || dictObj || {};

  if (!Array.isArray(scheduleList) || scheduleList.length === 0) {
    console.log('YAML de schedule vazio — nada a fazer.');
    return;
  }

  // 2) Normaliza entradas e calcula intervalo total
  let minDate = null;
  let maxDate = null;

  const items = [];
  scheduleList.forEach((entry) => {
    const isoDate = String(entry.date || '').trim();
    if (!isoDate) return;

    if (!minDate || isoDate < minDate) minDate = isoDate;
    if (!maxDate || isoDate > maxDate) maxDate = isoDate;

    // NOVO: pegamos a "fase" (título principal), atividades e observações a partir de meta.*
    const meta = entry.meta || {};
    const faseKey = safe(meta.fase);
    const atividadesKey = safe(meta.atividades);
    const observacoesKey = safe(meta.observacoes);
    const eventosPiKey = safe(meta.eventos_pi);

    // NOVO: cor do evento (words -> colorId)
    const colorId =
      resolveColorId_(meta.event_color || meta.color || entry.event_color);

    const faseTituloBase = resolveKey_(translations, faseKey) || faseKey || '(Fase não definida)';
    const atividadesTxt = resolveKey_(translations, atividadesKey) || atividadesKey;
    const observacoesTxt = resolveKey_(translations, observacoesKey) || observacoesKey;

    // PI Day / Sprint usados na descrição E AGORA NO TÍTULO TAMBÉM
    const piDay = safe(meta.pi_day || entry.pi_day);
    const sprint = safe(meta.sprint || entry.sprint);
    const dayInSprint = safe(meta.day_in_sprint || entry.day_in_sprint);

    // Descrição do evento principal (fase)
    const lines = [];
    if (atividadesTxt) lines.push(`Atividades: ${atividadesTxt}`);
    if (observacoesTxt) lines.push(`Observações: ${observacoesTxt}`);
    lines.push('');
    lines.push(`PI Day: ${piDay}`);
    lines.push(`Sprint: ${sprint}`);
    lines.push(`Dia na Sprint: ${dayInSprint}`);

    const description = lines.filter(Boolean).join('\n');

    // NOVO: monta sufixo de título com PI/Sprint
    const titleSuffixParts = [];
    if (piDay) titleSuffixParts.push(`PI ${piDay}`);
    if (sprint) titleSuffixParts.push(`Sprint ${sprint}`);
    const titleSuffix = titleSuffixParts.length ? ` — ${titleSuffixParts.join(' • ')}` : '';

    // Título da fase com PI/Sprint
    const faseTitulo = `${faseTituloBase}${titleSuffix}`;

    // Evento principal da fase (um por dia)
    items.push({
      kind: 'fase',
      isoDate,
      startDate: isoDate,
      endDate: addOneDay_(isoDate),
      sourceKey: `${isoDate}::fase`, // NOVO: chave estável por dia
      title: faseTitulo,
      description,
      colorId   // <- NOVO
    });

    // NOVO: Evento(s) de PI com agendamento próprio (se houver)
    if (eventosPiKey) {
      const eventosPiBaseTitulo = resolveKey_(translations, eventosPiKey) || eventosPiKey;

      // Título do evento de PI também com PI/Sprint
      const eventosPiTitulo = `${eventosPiBaseTitulo}${titleSuffix}`;

      const tw = resolveTimeWindow_(translations, eventosPiKey); // tenta achar horários

      if (tw && tw.start && tw.end) {
        // evento com horário
        items.push({
          kind: 'eventos_pi_timed',
          isoDate,
          startDateTime: composeDateTime_(isoDate, tw.start),
          endDateTime: composeDateTime_(isoDate, tw.end),
          timeZone: TIMEZONE,
          sourceKey: `${isoDate}::eventos_pi::${eventosPiKey}`,
          title: eventosPiTitulo,
          description: `Evento de PI\n\n${description}`.trim(),
          colorId   // <- NOVO
        });
      } else {
        // fallback: evento de dia inteiro separado
        items.push({
          kind: 'eventos_pi_allday',
          isoDate,
          startDate: isoDate,
          endDate: addOneDay_(isoDate),
          sourceKey: `${isoDate}::eventos_pi::${eventosPiKey}`,
          title: eventosPiTitulo,
          description: `Evento de PI\n\n${description}`.trim(),
          colorId   // <- NOVO
        });
      }
    }
  });

  if (!minDate || !maxDate) {
    console.log('Não foi possível determinar intervalo de datas. Abortando.');
    return;
  }

  const timeMin = `${minDate}T00:00:00Z`;
  const timeMax = `${addOneDay_(maxDate)}T00:00:00Z`;

  console.log(`Intervalo: ${minDate} → ${maxDate} | Itens: ${items.length}`);

  // 3) Carrega TODOS os eventos do script no intervalo (uma única varredura)
  const existingMap = loadExistingEventsMap_(CALENDAR_ID, timeMin, timeMax);
  console.log(`Eventos existentes carregados: ${existingMap.size}`);

  // 4) Upsert (patch/insert)
  let created = 0, updated = 0;
  const keepIds = new Set();
  const desiredSourceKeys = new Set();

  items.forEach((it, idx) => {
    desiredSourceKeys.add(it.sourceKey);
    const existing = existingMap.get(it.sourceKey);

    if (existing && existing.id) {
      // patch conforme tipo
      if (it.startDateTime && it.endDateTime) {
        patchTimedEvent_({
          calendarId: CALENDAR_ID,
          eventId: existing.id,
          title: it.title,
          description: it.description,
          startDateTime: it.startDateTime,
          endDateTime: it.endDateTime,
          timeZone: it.timeZone || TIMEZONE,
          colorId: it.colorId   // <- NOVO
        });
      } else {
        patchAllDayEvent_({
          calendarId: CALENDAR_ID,
          eventId: existing.id,
          title: it.title,
          description: it.description,
          startDate: it.startDate,
          endDate: it.endDate,
          colorId: it.colorId   // <- NOVO
        });
      }
      keepIds.add(existing.id);
      updated++;
    } else {
      // insert conforme tipo
      let createdEv;
      if (it.startDateTime && it.endDateTime) {
        createdEv = insertTimedEvent_({
          calendarId: CALENDAR_ID,
          title: it.title,
          description: it.description,
          startDateTime: it.startDateTime,
          endDateTime: it.endDateTime,
          timeZone: it.timeZone || TIMEZONE,
          colorId: it.colorId,  // <- NOVO
          extendedPrivate: { source: 'pi-schedule', sourceKey: it.sourceKey }
        });
      } else {
        createdEv = insertAllDayEvent_({
          calendarId: CALENDAR_ID,
          title: it.title,
          description: it.description,
          startDate: it.startDate,
          endDate: it.endDate,
          colorId: it.colorId,  // <- NOVO
          extendedPrivate: { source: 'pi-schedule', sourceKey: it.sourceKey }
        });
      }
      if (createdEv && createdEv.id) keepIds.add(createdEv.id);
      created++;
    }

    if ((idx + 1) % 25 === 0) {
      console.log(`Progresso: ${idx + 1}/${items.length} (criados ${created}, atualizados ${updated})`);
    }
  });

  console.log(`Upsert concluído: criados=${created}, atualizados=${updated}`);

  // 5) Limpeza seletiva
  if (ENABLE_CLEANUP) {
    cleanupRemovedEventsBySourceKey_(CALENDAR_ID, desiredSourceKeys);
  }

  console.log('=== PI Sync: fim ===');
}

/***********************
 * HELPERS
 ***********************/
function ensureJsYaml_() {
  if (globalThis.jsyaml) return;
  const code = UrlFetchApp.fetch(JS_YAML_CDN, { muteHttpExceptions: true }).getContentText('UTF-8');
  eval(code);
  if (!globalThis.jsyaml) throw new Error('Falha ao carregar js-yaml.');
}

// Retry/backoff para 429/5xx
function withRetry_(fn, desc) {
  let attempt = 0;
  // eslint-disable-next-line no-constant-condition
  while (true) {
    try {
      const res = fn();
      Utilities.sleep(SLEEP_MS_BETWEEN_CALLS);
      return res;
    } catch (e) {
      const msg = String(e);
      const isRate = msg.includes('Rate Limit Exceeded') || msg.includes('User Rate Limit Exceeded') || msg.includes('quotaExceeded') || msg.includes('429');
      const is5xx = /\b5\d\d\b/.test(msg) || msg.includes('Backend Error');
      if ((isRate || is5xx) && attempt < MAX_RETRIES) {
        const wait = Math.floor((BASE_BACKOFF_MS * Math.pow(2, attempt)) * (0.75 + Math.random() * 0.5));
        console.warn(`withRetry: ${desc} falhou (${msg}). Retentativa em ${wait}ms... [tentativa ${attempt + 1}]`);
        Utilities.sleep(wait);
        attempt++;
        continue;
      }
      throw e;
    }
  }
}

function resolveKey_(dict, dotted) {
  if (!dotted) return '';
  const path = String(dotted).split('.').map(s => s.trim()).filter(Boolean);
  let cur = dict;
  for (let i = 0; i < path.length; i++) {
    if (cur && Object.prototype.hasOwnProperty.call(cur, path[i])) {
      cur = cur[path[i]];
    } else {
      return '';
    }
  }
  return typeof cur === 'string' ? cur : '';
}

// NOVO: retorna qualquer tipo (objeto/string) para uma chave pontilhada
function resolveAny_(dict, dotted) {
  if (!dotted) return null;
  const path = String(dotted).split('.').map(s => s.trim()).filter(Boolean);
  let cur = dict;
  for (let i = 0; i < path.length; i++) {
    if (cur && Object.prototype.hasOwnProperty.call(cur, path[i])) {
      cur = cur[path[i]];
    } else {
      return null;
    }
  }
  return cur;
}

// NOVO: tenta extrair janela de horário do dicionário
function resolveTimeWindow_(dict, dotted) {
  const obj = resolveAny_(dict, dotted);
  if (!obj || typeof obj !== 'object') return null;

  // tenta múltiplas convenções de campo
  const candidates = [
    { s: 'start', e: 'end' },
    { s: 'inicio', e: 'fim' },
    { s: 'hora_inicio', e: 'hora_fim' },
    { s: 'start_time', e: 'end_time' }
  ];
  for (const c of candidates) {
    const s = obj[c.s];
    const e = obj[c.e];
    if (typeof s === 'string' && typeof e === 'string') {
      return { start: normalizeTime_(s), end: normalizeTime_(e) };
    }
  }
  return null;
}

// NOVO: normaliza '9', '9:0', '09:00' -> '09:00'
function normalizeTime_(hhmm) {
  const m = String(hhmm).match(/^(\d{1,2})(?::?(\d{1,2}))?$/);
  if (!m) return null;
  const hh = String(Math.max(0, Math.min(23, parseInt(m[1], 10)))).padStart(2, '0');
  const mm = String(m[2] ? Math.max(0, Math.min(59, parseInt(m[2], 10))) : 0).padStart(2, '0');
  return `${hh}:${mm}`;
}

function addOneDay_(yyyy_mm_dd) {
  const [y, m, d] = yyyy_mm_dd.split('-').map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  dt.setUTCDate(dt.getUTCDate() + 1);
  const y2 = dt.getUTCFullYear();
  const m2 = String(dt.getUTCMonth() + 1).padStart(2, '0');
  const d2 = String(dt.getUTCDate()).padStart(2, '0');
  return `${y2}-${m2}-${d2}`;
}

function composeDateTime_(yyyy_mm_dd, hh_mm /* 'HH:MM' */) {
  // Calendar API aceita dateTime + timeZone; aqui devolvemos apenas a parte local (sem Z/offset)
  return `${yyyy_mm_dd}T${hh_mm}:00`;
}

function safe(v) {
  return (v === undefined || v === null) ? '' : String(v);
}

function fetchTextUtf8_(url) {
  const resp = UrlFetchApp.fetch(url, {
    muteHttpExceptions: true,
    headers: { 'Accept-Charset': 'utf-8' }
  });
  const bytes = resp.getContent();
  let text = Utilities.newBlob(bytes).getDataAsString('UTF-8');
  if (text && text.normalize) text = text.normalize('NFC');
  return text;
}

function hasMojibake_(s) {
  if (!s) return false;
  return /Ã.|�/.test(s);
}

/**
 * NOVO: converte strings de cor (PT/EN) em colorId do Google Calendar.
 * Docs: https://developers.google.com/calendar/api/v3/reference/colors/get
 * 1=Lavender, 2=Sage, 3=Grape, 4=Flamingo, 5=Banana, 6=Tangerine,
 * 7=Peacock, 8=Graphite, 9=Blueberry, 10=Basil, 11=Tomato
 */
function resolveColorId_(raw) {
  if (!raw) return undefined;
  const key = String(raw).trim().toLowerCase();

  const map = {
    'yellow': '5',
    'amarelo': '5',
    'green': '10',
    'verde': '10',
    'red': '11',
    'vermelho': '11',
    'blue': '9',
    'azul': '9',
    'orange': '6',
    'laranja': '6',

    // extras úteis:
    'sage': '2',
    'basil': '10',
    'banana': '5',
    'tangerine': '6',
    'blueberry': '9',
    'tomato': '11',
    'graphite': '8',
    'peacock': '7',
    'lavender': '1',
    'grape': '3',
    'flamingo': '4'
  };

  // se vier um número válido (1..11), respeita
  if (/^(?:[1-9]|10|11)$/.test(key)) return key;

  return map[key] || undefined;
}


/***********************
 * CARREGAMENTO EM LOTE
 ***********************/
// Retorna Map<sourceKey, { id, summary, start, end }>
function loadExistingEventsMap_(calendarId, timeMinRfc3339, timeMaxRfc3339) {
  const map = new Map();
  let pageToken = null;
  let total = 0;
  do {
    const resp = withRetry_(() => Calendar.Events.list(calendarId, {
      timeMin: timeMinRfc3339,
      timeMax: timeMaxRfc3339,
      showDeleted: false,
      singleEvents: true,
      privateExtendedProperty: 'source=pi-schedule',
      maxResults: 2500,
      pageToken
    }), 'Events.list(existingMap)');
    const items = (resp && resp.items) || [];
    items.forEach(ev => {
      const priv = (ev.extendedProperties && ev.extendedProperties.private) || {};
      const sk = priv.sourceKey;
      if (sk) {
        map.set(sk, { id: ev.id, summary: ev.summary, start: ev.start, end: ev.end });
      }
    });
    total += items.length;
    pageToken = resp.nextPageToken;
  } while (pageToken);
  console.log(`loadExistingEventsMap_: varridos ${total} eventos do script no intervalo.`);
  return map;
}

/***********************
 * UPSERT
 ***********************/
function patchAllDayEvent_({ calendarId, eventId, title, description, startDate, endDate, colorId }) {
  const patchBody = {
    summary: title,
    description: description,
    start: { date: startDate },
    end:   { date: endDate }
  };
  if (colorId) patchBody.colorId = colorId; // NOVO
  withRetry_(() => Calendar.Events.patch(patchBody, calendarId, eventId), `Events.patch(${eventId})`);
}

function insertAllDayEvent_({ calendarId, title, description, startDate, endDate, colorId, extendedPrivate }) {
  const body = {
    summary: title,
    description: description,
    start: { date: startDate },
    end:   { date: endDate },
    extendedProperties: { private: extendedPrivate || {} }
  };
  if (colorId) body.colorId = colorId; // NOVO
  return withRetry_(() => Calendar.Events.insert(body, calendarId), `Events.insert(${startDate}:${title})`);
}

// NOVO: eventos com horário
function patchTimedEvent_({ calendarId, eventId, title, description, startDateTime, endDateTime, timeZone, colorId }) {
  const patchBody = {
    summary: title,
    description: description,
    start: { dateTime: startDateTime, timeZone: timeZone || TIMEZONE },
    end:   { dateTime: endDateTime,   timeZone: timeZone || TIMEZONE }
  };
  if (colorId) patchBody.colorId = colorId; // NOVO
  withRetry_(() => Calendar.Events.patch(patchBody, calendarId, eventId), `Events.patch(${eventId})`);
}

function insertTimedEvent_({ calendarId, title, description, startDateTime, endDateTime, timeZone, colorId, extendedPrivate }) {
  const body = {
    summary: title,
    description: description,
    start: { dateTime: startDateTime, timeZone: timeZone || TIMEZONE },
    end:   { dateTime: endDateTime,   timeZone: timeZone || TIMEZONE },
    extendedProperties: { private: extendedPrivate || {} }
  };
  if (colorId) body.colorId = colorId; // NOVO
  return withRetry_(() => Calendar.Events.insert(body, calendarId), `Events.insert(${startDateTime}:${title})`);
}

/***********************
 * LIMPEZA GLOBAL POR SOURCE KEY
 ***********************/
// Lista TODOS os eventos criados pelo script, independentemente de data
function listAllScriptEvents_(calendarId) {
  const all = [];
  let pageToken = null;
  do {
    const resp = withRetry_(() => Calendar.Events.list(calendarId, {
      showDeleted: false,
      singleEvents: true,
      privateExtendedProperty: 'source=pi-schedule',
      maxResults: 2500,
      pageToken
    }), 'Events.list(listAllScriptEvents_)');

    const items = (resp && resp.items) || [];
    for (const ev of items) {
      all.push(ev);
    }
    pageToken = resp.nextPageToken;
  } while (pageToken);
  console.log(`listAllScriptEvents_: total ${all.length} evento(s) do script.`);
  return all;
}

function cleanupRemovedEventsBySourceKey_(calendarId, desiredSourceKeysSet) {
  const allScriptEvents = listAllScriptEvents_(calendarId);

  const toDelete = [];
  for (const ev of allScriptEvents) {
    const priv = (ev.extendedProperties && ev.extendedProperties.private) || {};
    const sk = priv.sourceKey;
    if (!sk) continue; // segurança: só mexe no que tem sourceKey
    if (!desiredSourceKeysSet.has(sk)) {
      toDelete.push({
        id: ev.id,
        summary: ev.summary,
        start: ev.start,
        end: ev.end,
        sourceKey: sk
      });
    }
  }

  if (!toDelete.length) {
    console.log('Limpeza: nada para remover.');
    return;
  }

  console.log(`Limpeza: ${toDelete.length} evento(s) a remover.`);
  toDelete.forEach((e, idx) => {
    const info = `[${e.id}] "${e.summary}" (sourceKey=${e.sourceKey})`;
    if (DRY_RUN) {
      console.log(`DRY_RUN: apagaria ${info}`);
    } else {
      try {
        withRetry_(() => Calendar.Events.remove(calendarId, e.id), `Events.remove(${e.id})`);
        if ((idx + 1) % 25 === 0) console.log(`Limpeza progresso: ${idx + 1}/${toDelete.length}`);
      } catch (err) {
        console.error(`Falha ao remover ${info}: ${err}`);
      }
    }
  });
  console.log('Limpeza finalizada.');
}


/***********************
 * CRON (opcional)
 ***********************/
function createDailyTrigger() {
  ScriptApp.newTrigger('syncPlanningIntervalSchedule')
    .timeBased()
    .atHour(6)
    .everyDays(1)
    .create();
}
