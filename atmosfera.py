"""
Modelagem atmosférica e comparação entre os três cenários de irradiância.

  A) topo da atmosfera      G0h = Gon · cos θz           (equações do solar.py)
  B) céu limpo no solo      modelo de Ineichen-Perez     (implementado aqui)
  C) real no solo           GHI estimada pelo Open-Meteo (consultada em meteo.py)

Todas as grandezas comparadas são GHI (superfície horizontal), em W/m², como
MÉDIA HORÁRIA do mesmo intervalo [h, h+1) no horário local. Nenhuma equação
astronômica é refeita: a posição do Sol vem das funções do solar.py.

Referências:
  Ineichen & Perez (2002), "A new airmass independent formulation for the
      Linke turbidity coefficient", Solar Energy 73(3).
  Kasten & Young (1989), massa de ar relativa.
  Kasten (1996), "The Linke turbidity factor based on improved values of the
      integral Rayleigh optical thickness", Solar Energy 56(3).
  Molineaux, Ineichen & O'Neill (1998), profundidade óptica de aerossóis de
      banda larga.
"""
import math

import solar

P0 = 101325.0           # pressão padrão ao nível do mar (Pa)
DEN_MIN = 10.0          # W/m²: abaixo disso os percentuais não são calculados
PW_PADRAO_MM = 20.0     # vapor d'água padrão assumido (mm = kg/m²), se não houver dado
AOD550_PADRAO = 0.10    # AOD a 550 nm padrão assumido, se não houver dado
FATOR_AOD_BB = 0.84     # AOD banda larga ≈ 0,84 · AOD550 (Ångström α = 1,3; Molineaux et al.)
N_SUB = 12              # subamostras por hora (a cada 5 min) para a média horária


# --------------------------------------------------------------------------
# Atmosfera: pressão, massa de ar e turbidez de Linke
# --------------------------------------------------------------------------
def pressao_padrao(altitude_m: float) -> float:
    """Pressão (Pa) da atmosfera padrão na altitude dada."""
    return P0 * (1.0 - 2.25577e-5 * altitude_m) ** 5.25588


def massa_ar_relativa(zenite_graus: float):
    """Massa de ar relativa (Kasten & Young, 1989). None se o Sol está abaixo do horizonte."""
    if zenite_graus >= 90.0:
        return None
    return 1.0 / (math.cos(math.radians(zenite_graus)) + 0.50572 * (96.07995 - zenite_graus) ** -1.6364)


def turbidez_linke(pw_mm: float, aod550: float) -> float:
    """
    Turbidez de Linke para massa de ar 2 (Kasten, 1996), a partir do vapor
    d'água precipitável (mm) e da profundidade óptica de aerossóis a 550 nm.
    """
    pw_cm = max(pw_mm, 0.5) / 10.0
    aod_bb = FATOR_AOD_BB * max(aod550, 0.0)
    m = 2.0
    delta_cda = -0.101 + 0.235 * m ** -0.16
    delta_w = 0.112 * m ** -0.55 * pw_cm ** 0.34
    return (9.4 + 0.9 * m) * (delta_cda + delta_w + aod_bb)


# --------------------------------------------------------------------------
# Modelo de céu limpo de Ineichen-Perez
# --------------------------------------------------------------------------
def ineichen_perez(cosz: float, gon: float, altitude: float, tl: float, p_pa: float):
    """
    Retorna (GHI, DNI, DHI) de céu limpo, em W/m², para um instante.
    cosz: cosseno do ângulo zenital; gon: irradiância extraterrestre normal (Etapa 8).
    """
    if cosz <= 0:
        return 0.0, 0.0, 0.0
    zen = math.degrees(math.acos(min(1.0, cosz)))
    am = massa_ar_relativa(zen)
    if am is None:
        return 0.0, 0.0, 0.0
    am_abs = am * p_pa / P0

    fh1 = math.exp(-altitude / 8000.0)
    fh2 = math.exp(-altitude / 1250.0)
    cg1 = 5.09e-05 * altitude + 0.868
    cg2 = 3.92e-05 * altitude + 0.0387

    ghi = cg1 * gon * cosz * max(math.exp(-cg2 * am_abs * (fh1 + fh2 * (tl - 1.0))), 0.0)

    b = 0.664 + 0.163 / fh1
    bnci = gon * max(b * math.exp(-0.09 * am_abs * (tl - 1.0)), 0.0)
    bnci_2 = (1.0 - (0.1 - 0.2 * math.exp(-tl)) / (0.1 + 0.882 / fh1)) / cosz
    bnci_2 = ghi * min(max(bnci_2, 0.0), 1e20)
    dni = min(bnci, bnci_2)
    dhi = max(ghi - dni * cosz, 0.0)
    return ghi, dni, dhi


# --------------------------------------------------------------------------
# Posição solar em qualquer instante (reaproveita as equações do solar.py)
# --------------------------------------------------------------------------
def cosz_no_instante(res, t_local: float) -> float:
    """cos θz no horário local t_local (horas decimais), com δ, CL e E do Resultado."""
    off = (res.cl + res.E) / 60.0
    t_sol = (t_local + off) % 24.0
    h = 15.0 * (t_sol - 12.0)
    return solar.seno_altura(res.lat, res.delta, h)


def media_hora(res, h_ini: int, altitude: float, tl: float, p_pa: float) -> dict:
    """Médias horárias (intervalo [h_ini, h_ini+1)) de topo, GHI/DNI/DHI de céu limpo e cos θz."""
    top = ghi = dni = dhi = cz_soma = 0.0
    for k in range(N_SUB):
        cz = cosz_no_instante(res, h_ini + (k + 0.5) / N_SUB)
        if cz > 0:
            top += res.gon * cz
            g, d, f = ineichen_perez(cz, res.gon, altitude, tl, p_pa)
            ghi += g
            dni += d
            dhi += f
            cz_soma += cz
    n = float(N_SUB)
    return {"top": top / n, "ghi": ghi / n, "dni": dni / n, "dhi": dhi / n, "cosz": cz_soma / n}


# --------------------------------------------------------------------------
# Perdas e índice relativo (sempre com sinal; nunca forçadas a positivo)
# --------------------------------------------------------------------------
def _pct(num, den):
    if num is None or den is None or den < DEN_MIN:
        return None
    return num / den * 100.0


def calcular_perdas(top, ceu, real) -> dict:
    """
    Três métricas separadas, todas com a mesma grandeza (GHI, W/m², média horária):
      att_cs  = topo − céu limpo       (atenuação atmosférica em céu limpo)
      red_ad  = céu limpo − real       (redução adicional em relação ao céu limpo)
      att_tot = topo − real            (atenuação total)
      indice  = real / céu limpo · 100 (índice relativo ao céu limpo)
    Cada valor é (W/m², %), com % = None quando o denominador é menor que DEN_MIN.
    """
    out = {"att_cs": None, "red_ad": None, "att_tot": None, "indice": None, "avisos": []}
    if top is None or ceu is None:
        return out
    out["att_cs"] = (top - ceu, _pct(top - ceu, top))
    if top < DEN_MIN:
        out["avisos"].append(
            "Sol muito baixo ou abaixo do horizonte nesse intervalo: os percentuais não são calculados."
        )
    if real is None:
        return out
    out["red_ad"] = (ceu - real, _pct(ceu - real, ceu))
    out["att_tot"] = (top - real, _pct(top - real, top))
    out["indice"] = _pct(real, ceu)
    if ceu >= DEN_MIN and real > ceu:
        out["avisos"].append(
            "A irradiância real está acima da estimativa de céu limpo (a diferença é mostrada com sinal). "
            "Possíveis causas: realce por nuvens, parâmetros atmosféricos do modelo (aerossóis, vapor d'água), "
            "diferença de resolução espacial ou incerteza das fontes."
        )
    return out


def consistencia_componentes(ghi, dni, dhi, cosz_med):
    """
    Verifica aproximadamente GHI = DHI + DNI · cos θz (médias horárias).
    Retorna dict com GHI calculada, resíduo e status; None se faltar dado.
    """
    if None in (ghi, dni, dhi) or cosz_med is None:
        return None
    calc = dhi + dni * cosz_med
    res = ghi - calc
    if ghi < DEN_MIN:
        return {"calc": calc, "residuo": res, "pct": None, "ok": True, "nota": "Sol baixo: verificação não aplicável."}
    ok = abs(res) <= max(15.0, 0.10 * ghi)
    return {"calc": calc, "residuo": res, "pct": res / ghi * 100.0, "ok": ok, "nota": ""}


def energia_dia(serie, top):
    """
    kWh/m²·dia = soma(W/m² médios horários × 1 h) / 1000.
    Retorna (valor, n_lacunas); valor = None se houver horas com Sol sem dado.
    """
    lacunas = sum(1 for v, t in zip(serie, top) if v is None and t >= 0.5)
    if lacunas:
        return None, lacunas
    return sum(v for v in serie if v is not None) / 1000.0, 0


# --------------------------------------------------------------------------
# Análise de um dia inteiro
# --------------------------------------------------------------------------
def _serie(dia, grupo, nome):
    s = ((dia or {}).get(grupo) or {}).get(nome)
    return list(s) if s else [None] * 24


def _preencher(serie, padrao):
    """Valores por hora; faltas recebem a média do dia ou o padrão, sempre com a origem."""
    validos = [v for v in serie if v is not None]
    media = sum(validos) / len(validos) if validos else None
    vals, orig = [], []
    for v in serie:
        if v is not None:
            vals.append(v)
            orig.append("api")
        elif media is not None:
            vals.append(media)
            orig.append("média do dia")
        else:
            vals.append(padrao)
            orig.append("padrão")
    return vals, orig


def rotulo_origem(origens) -> str:
    s = set(origens)
    if s == {"api"}:
        return "dados da API"
    if s == {"padrão"}:
        return "valor padrão assumido"
    return "misto (API + média do dia/padrão assumido)"


def analisar_dia(res, om=None, aod=None, nasa=None) -> dict:
    """
    Monta, para as 24 horas locais do dia, os três cenários de GHI e os dados
    auxiliares. Sem dados online, o céu limpo usa atmosfera padrão (sinalizado).
    """
    altitude = (om or {}).get("elevacao")
    altitude_origem = "Open-Meteo (elevação do ponto)" if altitude is not None else "padrão assumido (0 m)"
    altitude = 0.0 if altitude is None else float(altitude)
    om_dia = (om or {}).get("dia")

    pw, pw_o = _preencher(_serie(om_dia, "inst", "total_column_integrated_water_vapour"), PW_PADRAO_MM)
    aod550, aod_o = _preencher((aod or {}).get("aod550") or [None] * 24, AOD550_PADRAO)
    p_hpa_api = _serie(om_dia, "inst", "surface_pressure")
    p_hpa, p_o = _preencher(p_hpa_api, pressao_padrao(altitude) / 100.0)

    an = {k: [] for k in ("h", "top", "ceu", "ceu_dni", "ceu_dhi", "cosz", "tl")}
    for h in range(24):
        tl = turbidez_linke(pw[h], aod550[h])
        m = media_hora(res, h, altitude, tl, p_hpa[h] * 100.0)
        an["h"].append(h)
        an["top"].append(m["top"])
        an["ceu"].append(m["ghi"])
        an["ceu_dni"].append(m["dni"])
        an["ceu_dhi"].append(m["dhi"])
        an["cosz"].append(m["cosz"])
        an["tl"].append(tl)

    an["altitude"] = altitude
    an["altitude_origem"] = altitude_origem
    an["pw_mm"], an["aod550"], an["p_hpa"] = pw, aod550, p_hpa
    an["origem"] = {"pw": rotulo_origem(pw_o), "aod": rotulo_origem(aod_o), "p": rotulo_origem(p_o)}

    # real (Open-Meteo): médias da hora; instantâneos no início do intervalo
    for nome, chave in (("real", "shortwave_radiation"), ("real_dni", "direct_normal_irradiance"),
                        ("real_dhi", "diffuse_radiation"), ("real_dir_h", "direct_radiation"),
                        ("om_topo", "terrestrial_radiation"), ("om_gti", "global_tilted_irradiance")):
        an[nome] = _serie(om_dia, "rad", chave)
    for nome, chave in (("nuvens", "cloud_cover"), ("temp", "temperature_2m"),
                        ("umid", "relative_humidity_2m"), ("pressao", "surface_pressure"),
                        ("chuva_prev", "precipitation"), ("vapor", "total_column_integrated_water_vapour")):
        an[nome] = _serie(om_dia, "inst", chave) if chave != "precipitation" else _serie(om_dia, "rad", chave)
    an["aod_api"] = list((aod or {}).get("aod550") or [None] * 24)

    # NASA POWER (mesma grandeza e mesmo intervalo; nunca substitui o Open-Meteo)
    nd = (nasa or {}).get("dia")
    an["nasa"] = None
    if nd:
        an["nasa"] = {k: list(v) for k, v in nd.items()}
    return an


def resumo_perdas_hora(an: dict, h: int) -> dict:
    p = calcular_perdas(an["top"][h], an["ceu"][h], an["real"][h])
    p["consist"] = consistencia_componentes(
        an["real"][h], an["real_dni"][h], an["real_dhi"][h], an["cosz"][h])
    return p
