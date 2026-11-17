"""
Clientes das APIs gratuitas Open-Meteo e NASA POWER (somente biblioteca padrão).

As APIs COMPLEMENTAM o modelo: não substituem nenhum cálculo astronômico.
Convenções de tempo (para comparar as fontes na mesma base):
  * tudo é convertido para o horário local do aplicativo (UTC + deslocamento do fuso);
  * cada valor horário é identificado pelo INÍCIO do intervalo [h, h+1);
  * Open-Meteo: radiação = média da hora anterior (rótulo = fim do intervalo),
    então o valor do intervalo [h, h+1) é o rótulo h+1; temperatura, umidade,
    pressão e nuvens são instantâneos (rótulo = instante h);
  * NASA POWER: o índice horário já é o início do intervalo (UTC).
Nenhuma lacuna é preenchida: dado ausente permanece None.
"""
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta, timezone

TIMEOUT = 25
CABECALHO = {"User-Agent": "simulador-solar/2.0 (projeto didatico)"}

OM_PREVISAO = "https://api.open-meteo.com/v1/forecast"
OM_HISTORICO = "https://archive-api.open-meteo.com/v1/archive"
OM_AR = "https://air-quality-api.open-meteo.com/v1/air-quality"
NASA_HORARIO = "https://power.larc.nasa.gov/api/temporal/hourly/point"

# variáveis Open-Meteo cujo valor é a MÉDIA/SOMA DA HORA ANTERIOR
PREV = {
    "precipitation", "shortwave_radiation", "direct_normal_irradiance",
    "diffuse_radiation", "direct_radiation", "terrestrial_radiation",
    "global_tilted_irradiance",
}
OM_BASE = [
    "temperature_2m", "relative_humidity_2m", "surface_pressure", "cloud_cover",
    "precipitation", "shortwave_radiation", "direct_normal_irradiance", "diffuse_radiation",
]
OM_OPCIONAIS = ["total_column_integrated_water_vapour", "direct_radiation", "terrestrial_radiation"]

NASA_PARAMS = [
    "ALLSKY_SFC_SW_DWN", "ALLSKY_SFC_SW_DNI", "ALLSKY_SFC_SW_DIFF",
    "CLRSKY_SFC_SW_DWN", "TOA_SW_DWN", "T2M", "RH2M", "PS",
]


class ErroAPI(Exception):
    """Falha ao consultar uma API (conexão, resposta ou intervalo de datas)."""


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
def _get_json(url: str, params: dict) -> dict:
    consulta = urllib.parse.urlencode(params, safe=",")
    req = urllib.request.Request(f"{url}?{consulta}", headers=CABECALHO)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            corpo = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:  # noqa: BLE001
            corpo = ""
        raise ErroAPI(f"a API respondeu com erro {e.code}. {corpo}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ErroAPI("não foi possível conectar à API (sem internet ou serviço indisponível).") from e
    except json.JSONDecodeError as e:
        raise ErroAPI("resposta da API em formato inesperado.") from e


def _checar_fuso(desloc_h: float):
    if abs(desloc_h - round(desloc_h)) > 1e-9:
        raise ErroAPI(
            "o fuso tem deslocamento fracionário (ex.: UTC+5:30); a comparação horária "
            "com as APIs não é suportada nesse caso."
        )


def _agora_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _montar_dia(tempos_local, valores: dict, data: date, prev=frozenset()) -> dict:
    """
    Reorganiza séries em 24 posições (h = hora de INÍCIO do intervalo local).
    Variáveis em `prev` usam o rótulo do FIM do intervalo (h+1); as demais, o instante h.
    """
    indice = {t: i for i, t in enumerate(tempos_local)}
    base = datetime.combine(data, time(0))
    rad, inst = {}, {}
    for nome, vals in valores.items():
        saida = []
        for h in range(24):
            rotulo = base + timedelta(hours=h + (1 if nome in prev else 0))
            i = indice.get(rotulo)
            saida.append(vals[i] if i is not None and i < len(vals) else None)
        (rad if nome in prev else inst)[nome] = saida
    return {"rad": rad, "inst": inst}


# --------------------------------------------------------------------------
# Open-Meteo
# --------------------------------------------------------------------------
def escolher_endpoint(data: date, hoje: date) -> str:
    dias = (hoje - data).days
    if dias < -15:
        raise ErroAPI("a previsão do Open-Meteo alcança no máximo 16 dias à frente de hoje.")
    return "historico" if dias > 7 else "previsao"


def _tempos_locais(h: dict, desloc_h: float):
    return [datetime.strptime(s, "%Y-%m-%dT%H:%M") + timedelta(hours=desloc_h) for s in h["time"]]


def consultar_open_meteo(lat, lon, data: date, desloc_h: float, tilt=None, azimute_om=None, hoje=None) -> dict:
    """
    Dados horários do Open-Meteo para o dia local `data`.
    Histórico (> 7 dias): reanálise ERA5. Recente/futuro: modelos numéricos de previsão.
    Variáveis opcionais que não existirem para o ponto/data são apenas omitidas.
    """
    _checar_fuso(desloc_h)
    hoje = hoje or _agora_utc().date()
    modo = escolher_endpoint(data, hoje)
    url = OM_HISTORICO if modo == "historico" else OM_PREVISAO

    ini, fim = data - timedelta(days=1), data + timedelta(days=1)
    if modo == "previsao":
        fim = min(fim, hoje + timedelta(days=15))
    else:
        fim = min(fim, hoje - timedelta(days=7))

    def chamar(variaveis, extra=None):
        p = {
            "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
            "start_date": ini.isoformat(), "end_date": fim.isoformat(),
            "hourly": ",".join(variaveis), "timezone": "GMT",
        }
        p.update(extra or {})
        return _get_json(url, p)

    d = chamar(OM_BASE)
    if "hourly" not in d or "time" not in d["hourly"]:
        raise ErroAPI("o Open-Meteo não retornou dados horários para esse ponto e data.")
    tempos = _tempos_locais(d["hourly"], desloc_h)
    valores = {v: d["hourly"].get(v) for v in OM_BASE if d["hourly"].get(v) is not None}
    unidades = dict(d.get("hourly_units") or {})

    ausentes = []
    for var in OM_OPCIONAIS:  # uma consulta por variável: a falha de uma não afeta as outras
        try:
            o = chamar([var])
            if o.get("hourly", {}).get(var) is not None and len(o["hourly"]["time"]) == len(tempos):
                valores[var] = o["hourly"][var]
                unidades.update(o.get("hourly_units") or {})
            else:
                ausentes.append(var)
        except ErroAPI:
            ausentes.append(var)
    if tilt is not None and azimute_om is not None:
        try:
            o = chamar(["global_tilted_irradiance"], {"tilt": f"{tilt:.1f}", "azimuth": f"{azimute_om:.1f}"})
            if o.get("hourly", {}).get("global_tilted_irradiance") is not None and len(o["hourly"]["time"]) == len(tempos):
                valores["global_tilted_irradiance"] = o["hourly"]["global_tilted_irradiance"]
            else:
                ausentes.append("global_tilted_irradiance")
        except ErroAPI:
            ausentes.append("global_tilted_irradiance")

    dia = _montar_dia(tempos, valores, data, PREV)

    # natureza de cada dado (nunca "medição local")
    agora = _agora_utc()
    tipos = []
    for h in range(24):
        if modo == "historico":
            tipos.append("Histórico (reanálise ERA5)")
        else:
            fim_utc = datetime.combine(data, time(0)) + timedelta(hours=h + 1) - timedelta(hours=desloc_h)
            tipos.append("Previsto (modelo numérico)" if fim_utc > agora else "Estimado (análise de modelo numérico)")

    return {
        "fonte": "Open-Meteo · reanálise ERA5" if modo == "historico" else "Open-Meteo · modelos numéricos",
        "modo": modo,
        "elevacao": d.get("elevation"),
        "dia": dia,
        "unidades": unidades,
        "ausentes": ausentes,
        "tipo_por_hora": tipos,
        "consultado_em": agora.strftime("%d/%m/%Y %H:%M UTC"),
    }


def consultar_aod(lat, lon, data: date, desloc_h: float, hoje=None):
    """
    Profundidade óptica de aerossóis a 550 nm (CAMS, via Open-Meteo Air Quality).
    Retorna None quando não houver dados (ex.: antes de ago/2022 ou muito à frente).
    """
    _checar_fuso(desloc_h)
    hoje = hoje or _agora_utc().date()
    if data < date(2022, 8, 1) or data > hoje + timedelta(days=6):
        return None
    ini, fim = data - timedelta(days=1), min(data + timedelta(days=1), hoje + timedelta(days=6))
    try:
        d = _get_json(OM_AR, {
            "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
            "hourly": "aerosol_optical_depth", "timezone": "GMT",
            "start_date": ini.isoformat(), "end_date": fim.isoformat(),
        })
        tempos = _tempos_locais(d["hourly"], desloc_h)
        dia = _montar_dia(tempos, {"aod": d["hourly"]["aerosol_optical_depth"]}, data)
    except (ErroAPI, KeyError):
        return None
    serie = dia["inst"]["aod"]
    if all(v is None for v in serie):
        return None
    return {"aod550": serie, "fonte": "CAMS (aerossóis) via Open-Meteo Air Quality",
            "resolucao": "≈ 0,4° (≈ 45 km), 3 h"}


# --------------------------------------------------------------------------
# NASA POWER
# --------------------------------------------------------------------------
def _fator_wm2(unidade: str) -> float:
    return 1000.0 if unidade and "kW" in unidade else 1.0


def consultar_nasa_power(lat, lon, data: date, desloc_h: float, hoje=None) -> dict:
    """
    NASA POWER (horário, comunidade RE, UTC). Radiação em W/m² (média horária).
    Usada para validação e análise histórica; nunca substitui os dados do Open-Meteo.
    """
    _checar_fuso(desloc_h)
    hoje = hoje or _agora_utc().date()
    if data > hoje:
        raise ErroAPI("a NASA POWER fornece apenas dados históricos (não há previsão).")
    if data < date(2001, 1, 1):
        raise ErroAPI("a NASA POWER horária começa em 2001.")
    ini, fim = data - timedelta(days=1), min(data + timedelta(days=1), hoje)
    d = _get_json(NASA_HORARIO, {
        "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
        "start": ini.strftime("%Y%m%d"), "end": fim.strftime("%Y%m%d"),
        "community": "RE", "parameters": ",".join(NASA_PARAMS),
        "format": "JSON", "header": "true", "time-standard": "UTC",
    })
    try:
        par = d["properties"]["parameter"]
    except KeyError as e:
        raise ErroAPI("resposta da NASA POWER em formato inesperado.") from e
    fill = (d.get("header") or {}).get("fill_value", -999.0)
    unidades = {k: (v or {}).get("units", "") for k, v in (d.get("parameters") or {}).items()}

    nomes = {"ALLSKY_SFC_SW_DWN": "ghi", "ALLSKY_SFC_SW_DNI": "dni", "ALLSKY_SFC_SW_DIFF": "dhi",
             "CLRSKY_SFC_SW_DWN": "clear", "TOA_SW_DWN": "toa", "T2M": "t2m", "RH2M": "rh2m", "PS": "ps_hpa"}
    saida = {v: [None] * 24 for v in nomes.values()}
    for param, chave in nomes.items():
        serie = par.get(param) or {}
        k = _fator_wm2(unidades.get(param, "")) if chave in ("ghi", "dni", "dhi", "clear", "toa") else 1.0
        for rot, val in serie.items():
            if val is None or val == fill or val <= -990:
                continue
            ini_local = datetime.strptime(rot, "%Y%m%d%H") + timedelta(hours=desloc_h)
            if ini_local.date() != data:
                continue
            v = val * k
            if chave == "ps_hpa":
                v = val * 10.0  # kPa -> hPa
            saida[chave][ini_local.hour] = v

    disponivel = any(v is not None for v in saida["ghi"])
    return {
        "fonte": "NASA POWER (CERES SYN1deg / MERRA-2)",
        "dia": saida, "unidades": unidades, "disponivel": disponivel,
        "tipo": "Estimado (satélite/reanálise); não é medição local",
        "consultado_em": _agora_utc().strftime("%d/%m/%Y %H:%M UTC"),
    }
