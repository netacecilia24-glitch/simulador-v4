"""Seção ANÁLISE DA IRRADIÂNCIA ATMOSFÉRICA (interface Streamlit)."""
from datetime import date

import streamlit as st

import atmosfera
import graficos
import meteo
from atmosfera import DEN_MIN


# --------------------------------------------------------------------------
# Consultas (com cache, para não repetir requisições)
# --------------------------------------------------------------------------
@st.cache_data(ttl=1800, show_spinner=False)
def _om(lat, lon, data_iso, desloc, tilt, az_om):
    return meteo.consultar_open_meteo(lat, lon, date.fromisoformat(data_iso), desloc, tilt, az_om)


@st.cache_data(ttl=1800, show_spinner=False)
def _aod(lat, lon, data_iso, desloc):
    return meteo.consultar_aod(lat, lon, date.fromisoformat(data_iso), desloc)


@st.cache_data(ttl=3600, show_spinner=False)
def _nasa(lat, lon, data_iso, desloc):
    return meteo.consultar_nasa_power(lat, lon, date.fromisoformat(data_iso), desloc)


def _az_open_meteo(az_roof: float) -> float:
    """Azimute do telhado (Norte = 0°, horário) -> convenção do Open-Meteo (Sul = 0°, Leste = −90°)."""
    return ((az_roof - 180.0 + 180.0) % 360.0) - 180.0


def _consultar(res, az_roof, incl, usar_nasa):
    avisos, om, aod, nasa = [], None, None, None
    lat, lon, di, desloc = round(res.lat, 4), round(res.lon, 4), res.data.isoformat(), float(res.fuso.deslocamento)
    with st.spinner("Consultando Open-Meteo..."):
        try:
            om = _om(lat, lon, di, desloc, float(incl), _az_open_meteo(az_roof))
            aod = _aod(lat, lon, di, desloc)
        except meteo.ErroAPI as e:
            avisos.append(f"Open-Meteo indisponível: {e}")
        except Exception as e:  # noqa: BLE001
            avisos.append(f"Open-Meteo: erro inesperado ({e}).")
    if usar_nasa:
        with st.spinner("Consultando NASA POWER..."):
            try:
                nasa = _nasa(lat, lon, di, desloc)
                if not nasa["disponivel"]:
                    avisos.append("NASA POWER: sem dados horários para essa data e local (ainda não processados "
                                  "ou fora do período disponível).")
                    nasa = None
            except meteo.ErroAPI as e:
                avisos.append(f"NASA POWER indisponível: {e}")
            except Exception as e:  # noqa: BLE001
                avisos.append(f"NASA POWER: erro inesperado ({e}).")
    return om, aod, nasa, avisos


# --------------------------------------------------------------------------
# Formatação
# --------------------------------------------------------------------------
def _v(Tm, x, d=1, suf=""):
    return "—" if x is None else f"{Tm(x, d)}{suf}"


def _tabela_horaria(an, Tm):
    linhas = ["| Hora local | Topo (W/m²) | Céu limpo (W/m²) | Real (W/m²) | Atenuação céu limpo | "
              "Redução adicional | Índice relativo |", "|---|---|---|---|---|---|---|"]
    for h in an["h"]:
        if an["top"][h] < 0.5 and an["real"][h] is None:
            continue
        p = atmosfera.calcular_perdas(an["top"][h], an["ceu"][h], an["real"][h])
        a, r = p["att_cs"], p["red_ad"]
        linhas.append(
            f"| {h:02d}:00–{(h + 1) % 24:02d}:00 | {Tm(an['top'][h], 1)} | {Tm(an['ceu'][h], 1)} | "
            f"{_v(Tm, an['real'][h])} | {_v(Tm, a[1] if a else None, 1, ' %')} | "
            f"{_v(Tm, r[1] if r else None, 1, ' %')} | {_v(Tm, p['indice'], 1, ' %')} |"
        )
    return "\n".join(linhas)


def _tabela_energia(an, Tm):
    top = an["top"]
    itens = [
        ("Topo da atmosfera · este app (G0h)", top),
        ("Topo da atmosfera · Open-Meteo (terrestrial_radiation)", an["om_topo"]),
        ("Topo da atmosfera · NASA POWER (TOA_SW_DWN)", an["nasa"]["toa"] if an["nasa"] else None),
        ("Céu limpo · Ineichen-Perez (este app)", an["ceu"]),
        ("Céu limpo · NASA POWER (CLRSKY_SFC_SW_DWN)", an["nasa"]["clear"] if an["nasa"] else None),
        ("Real · Open-Meteo (GHI estimada)", an["real"]),
        ("Real · NASA POWER (ALLSKY_SFC_SW_DWN)", an["nasa"]["ghi"] if an["nasa"] else None),
    ]
    linhas = ["| Série (GHI, soma das 24 médias horárias) | Energia do dia (kWh/m²) | Observação |", "|---|---|---|"]
    energias = {}
    for nome, serie in itens:
        if serie is None or all(v is None for v in serie):
            continue
        e, lac = atmosfera.energia_dia(serie, top)
        energias[nome] = e
        obs = "" if lac == 0 else f"{lac} hora(s) com Sol sem dado: soma não calculada"
        linhas.append(f"| {nome} | {_v(Tm, e, 2)} | {obs} |")
    return "\n".join(linhas), energias


# --------------------------------------------------------------------------
# Seção
# --------------------------------------------------------------------------
def renderizar(res, az_roof, incl_roof, secao, cartao, Tm, mostrar, numero):
    secao(numero, "ANÁLISE DA IRRADIÂNCIA ATMOSFÉRICA",
          "Topo da atmosfera × céu limpo (Ineichen-Perez) × condições reais (Open-Meteo)")

    usar_nasa = st.checkbox("Incluir NASA POWER (validação histórica)", value=True, key="usar_nasa")
    om, aod, nasa, avisos_api = _consultar(res, az_roof, incl_roof, usar_nasa)
    an = atmosfera.analisar_dia(res, om, aod, nasa)
    h = res.hora.hour
    p = atmosfera.resumo_perdas_hora(an, h)
    intervalo = f"{h:02d}:00–{(h + 1) % 24:02d}:00"

    for a in avisos_api:
        st.warning(a)
    if om is None:
        st.info("Sem dados online: o céu limpo foi calculado com atmosfera padrão assumida "
                f"(vapor d'água {Tm(atmosfera.PW_PADRAO_MM, 0)} mm, AOD 550 nm = {Tm(atmosfera.AOD550_PADRAO, 2)}, "
                "pressão e altitude padrão) e a irradiância real não está disponível.")

    st.markdown(f"**Intervalo analisado:** {res.data:%d/%m/%Y}, {intervalo} (horário local). "
                "Todas as grandezas são **GHI**, em W/m², como **média horária** do mesmo intervalo.")

    # --- três cards principais -------------------------------------------
    real = an["real"][h]
    tipo_real = om["tipo_por_hora"][h] if om else "indisponível"
    st.markdown(
        '<div class="cards">'
        + cartao("Irradiância no topo da atmosfera", Tm(an["top"][h], 1),
                 "G0h = Gon · cos θz · sem atmosfera", "#F5B700")
        + cartao("Irradiância de céu limpo no solo", Tm(an["ceu"][h], 1),
                 f"Ineichen-Perez · TL = {Tm(an['tl'][h], 2)} · atmosfera: {an['origem']['pw']}", "#0EA5E9")
        + cartao("Irradiância real no solo", _v(Tm, real, 1),
                 f"{tipo_real} · estimativa da fonte, não medição local", "#F97316")
        + "</div>",
        unsafe_allow_html=True,
    )

    # --- três indicadores -------------------------------------------------
    def ind(rotulo, par, base, cor):
        if par is None:
            return cartao(rotulo, "—", "sem dado real para esse intervalo" if "real" in base else "indefinido", cor, " %")
        w, pct = par
        desc = f"{Tm(w, 1)} W/m² · {base}" + ("" if pct is not None else " · % indefinido (referência < 10 W/m²)")
        return cartao(rotulo, _v(Tm, pct, 1), desc, cor, " %")

    st.markdown(
        '<div class="cards">'
        + ind("Atenuação atmosférica em céu limpo", p["att_cs"], "topo − céu limpo", "#F5B700")
        + ind("Redução adicional em relação ao céu limpo", p["red_ad"], "céu limpo − real", "#0EA5E9")
        + ind("Atenuação total em relação ao topo", p["att_tot"], "topo − real", "#F97316")
        + "</div>",
        unsafe_allow_html=True,
    )
    c1, c2 = st.columns([1, 3])
    c1.metric("Índice relativo ao céu limpo", _v(Tm, p["indice"], 1, " %"))
    c2.caption(
        "Índice = GHI real ÷ GHI de céu limpo. É um índice **relativo à referência de céu limpo**, não uma "
        "medida pura de transparência atmosférica: depende também da nebulosidade e das incertezas do modelo. "
        "A diferença entre céu limpo e real **não deve ser atribuída automaticamente às nuvens** (aerossóis, "
        "umidade e incertezas das fontes também contribuem). Os percentuais não se somam: "
        "(1 − atenuação em céu limpo) × (1 − redução adicional) = 1 − atenuação total."
    )
    for a in p["avisos"]:
        st.info(a)

    # --- consistência dos componentes -------------------------------------
    cons = p["consist"]
    if cons:
        simb = "✔" if cons["ok"] else "⚠"
        extra = cons["nota"] or (f"resíduo {Tm(cons['residuo'], 1)} W/m² ({_v(Tm, cons['pct'], 1, ' %')})")
        st.caption(f"{simb} Consistência GHI ≈ DHI + DNI·cos θz (Open-Meteo, média horária): "
                   f"GHI = {Tm(an['real'][h], 1)} · DHI + DNI·cos θz = {Tm(cons['calc'], 1)} W/m² · {extra}. "
                   "Verificação aproximada (médias horárias, arredondamentos e convenções da fonte).")
        if not cons["ok"]:
            st.warning("Os componentes não fecham dentro da tolerância (10% ou 15 W/m²). A fonte pode usar outra "
                       "convenção temporal ou separar componentes por modelo; trate a comparação com cautela.")

    # --- informações meteorológicas ----------------------------------------
    st.markdown("##### Condições meteorológicas")
    cols = st.columns(6)
    cols[0].metric("Nebulosidade", _v(Tm, an["nuvens"][h], 0, " %"))
    cols[1].metric("Temperatura", _v(Tm, an["temp"][h], 1, " °C"))
    cols[2].metric("Umidade relativa", _v(Tm, an["umid"][h], 0, " %"))
    cols[3].metric("Pressão", _v(Tm, an["pressao"][h], 1, " hPa"))
    cols[4].metric("Precipitação (1 h)", _v(Tm, an["chuva_prev"][h], 1, " mm"))
    cols[5].metric("Vapor d'água (coluna)", _v(Tm, an["vapor"][h], 1, " mm"))
    aod_h = an["aod_api"][h]
    st.caption(
        f"AOD 550 nm: {_v(Tm, aod_h, 3)} ({an['origem']['aod']}) · altitude: {Tm(an['altitude'], 0)} m "
        f"({an['altitude_origem']}) · pressão e vapor d'água do modelo: {an['origem']['p']}/{an['origem']['pw']}. "
        "Temperatura, umidade, pressão e nebulosidade são valores instantâneos no início do intervalo."
        + (f" · Plano do telhado (GTI, só referência; não substitui a GHI): {_v(Tm, an['om_gti'][h], 1, ' W/m²')}."
           if any(v is not None for v in an["om_gti"]) else "")
    )

    # --- gráficos ----------------------------------------------------------
    abas = ["Três cenários", "Redução adicional", "Componentes (DNI, DHI, GHI)", "Tabela horária"]
    if an["nasa"]:
        abas.insert(3, "NASA POWER × Open-Meteo")
    tabs = st.tabs(abas)
    with tabs[0]:
        mostrar(graficos.grafico_atmosfera(an, h))
    with tabs[1]:
        mostrar(graficos.grafico_reducao(an, h))
    with tabs[2]:
        mostrar(graficos.grafico_componentes(an, h))
    if an["nasa"]:
        with tabs[3]:
            mostrar(graficos.grafico_nasa(an, h))
            st.caption("Ambas as fontes são horárias e foram colocadas no mesmo intervalo local. As resoluções "
                       "espaciais diferem e a NASA POWER não substitui o Open-Meteo: serve de validação.")
    with tabs[-1]:
        st.markdown(_tabela_horaria(an, Tm))
        st.caption("Horas sem dado real aparecem com “—” e não são preenchidas.")

    # --- energia do dia e validação ----------------------------------------
    with st.expander("Energia do dia e validação entre fontes"):
        tabela, energias = _tabela_energia(an, Tm)
        st.markdown(tabela)
        e_top = energias.get("Topo da atmosfera · este app (G0h)")
        e_ceu = energias.get("Céu limpo · Ineichen-Perez (este app)")
        e_real = energias.get("Real · Open-Meteo (GHI estimada)")
        if e_top and e_ceu is not None:
            m1, m2, m3 = st.columns(3)
            m1.metric("Atenuação em céu limpo (dia)", f"{Tm((e_top - e_ceu) / e_top * 100, 1)} %")
            if e_real is not None and e_ceu > 0:
                m2.metric("Redução adicional (dia)", f"{Tm((e_ceu - e_real) / e_ceu * 100, 1)} %")
                m3.metric("Atenuação total (dia)", f"{Tm((e_top - e_real) / e_top * 100, 1)} %")
        st.caption("Energia = soma das 24 médias horárias (W/m² × 1 h) ÷ 1000. Só é calculada quando não há "
                   "lacunas nas horas com Sol, para não preencher dados artificialmente.")

    # --- origem dos dados ---------------------------------------------------
    with st.expander("Origem, natureza e limitações dos dados"):
        if om:
            st.markdown(
                f"- **{om['fonte']}** — consultado em {om['consultado_em']}. "
                f"Natureza do dado neste intervalo: **{tipo_real}**. Resolução temporal: horária. "
                "Resolução espacial: depende do modelo (ERA5 ≈ 25 km; modelos de previsão ≈ 9–25 km).\n"
                "- A GHI/DNI/DHI do Open-Meteo vem de **modelos numéricos/reanálise**; **não é medição local** "
                "(a menos que exista uma estação de medição, o que esta fonte não garante).\n"
                + (f"- Variáveis opcionais indisponíveis para esse ponto/data: {', '.join(om['ausentes'])}.\n"
                   if om["ausentes"] else "")
            )
        else:
            st.markdown("- Open-Meteo: sem dados nesta consulta.")
        if aod:
            st.markdown(f"- **{aod['fonte']}** — {aod['resolucao']}. AOD a 550 nm (modelo, não medição local).")
        else:
            st.markdown("- Aerossóis (AOD): sem dados para essa data/local; foi usado AOD 550 nm padrão assumido = "
                        f"{Tm(atmosfera.AOD550_PADRAO, 2)}.")
        if nasa:
            st.markdown(f"- **{nasa['fonte']}** — consultado em {nasa['consultado_em']}. {nasa['tipo']}. Horária; "
                        "resolução espacial ≈ 0,5°–1° (muito mais grossa que a do Open-Meteo). "
                        f"Unidades informadas pela API: {nasa['unidades'].get('ALLSKY_SFC_SW_DWN', 'W/m²')}.")
        st.markdown(
            "- Ozônio: o modelo de Ineichen-Perez não o trata separadamente; seu efeito médio está embutido na "
            "turbidez de Linke. Nuvens **não** são usadas como fator percentual: o cenário “real” vem direto da fonte.\n"
            "- Limitações: resolução espacial grosseira, incerteza dos modelos de radiação, AOD global de baixa "
            "resolução e valores padrão quando faltam dados (sempre sinalizados).\n"
            "- Créditos: dados meteorológicos de Open-Meteo.com (CC BY 4.0; uso gratuito não comercial), aerossóis "
            "do Copernicus Atmosphere Monitoring Service (CAMS) e dados da NASA POWER (NASA Langley Research Center)."
        )

    # --- metodologia ----------------------------------------------------------
    with st.expander("Equações implementadas (céu limpo e perdas)"):
        st.markdown("**A. Topo da atmosfera** (Etapas 8 e 9, inalteradas):")
        st.latex(r"G_{0h} = G_{on}\,\cos\theta_z \qquad (\text{somente com o Sol acima do horizonte})")
        st.markdown("**B. Céu limpo no solo, modelo de Ineichen-Perez** (Ineichen & Perez, 2002):")
        st.latex(r"m_r = \frac{1}{\cos\theta_z + 0{,}50572\,(96{,}07995^\circ - \theta_z)^{-1{,}6364}}, \qquad m_a = m_r\,\frac{p}{p_0}")
        st.latex(r"GHI_{cs} = c_{g1}\,G_{on}\,\cos\theta_z\;\exp\!\left[-c_{g2}\,m_a\,\big(f_{h1} + f_{h2}(T_L - 1)\big)\right]")
        st.latex(r"c_{g1} = 5{,}09{\cdot}10^{-5}z + 0{,}868,\quad c_{g2} = 3{,}92{\cdot}10^{-5}z + 0{,}0387,\quad "
                 r"f_{h1} = e^{-z/8000},\quad f_{h2} = e^{-z/1250}")
        st.markdown("DNI e DHI de céu limpo seguem as relações empíricas do mesmo modelo "
                    "(DHI = GHI − DNI · cos θz). **Turbidez de Linke** (massa de ar 2, Kasten, 1996), "
                    "com vapor d'água *w* (cm) e AOD de banda larga τₐ ≈ 0,84 · AOD550 (Molineaux et al., 1998; "
                    "Ångström α = 1,3 assumido):")
        st.latex(r"T_L = (9{,}4 + 0{,}9\,m)\left[-0{,}101 + 0{,}235\,m^{-0{,}16} + 0{,}112\,m^{-0{,}55}\,w^{0{,}34} + \tau_a\right],\quad m = 2")
        st.markdown("**C. Real no solo:** GHI estimada pelo Open-Meteo (média da hora anterior, convertida ao "
                    "intervalo local). **Perdas** (mesma grandeza, superfície, unidade e intervalo):")
        st.latex(r"\Delta_{cs} = GHI_{topo} - GHI_{cs},\qquad \Delta_{cs}(\%) = \frac{GHI_{topo} - GHI_{cs}}{GHI_{topo}}\times 100")
        st.latex(r"\Delta_{ad} = GHI_{cs} - GHI_{real},\qquad \Delta_{ad}(\%) = \frac{GHI_{cs} - GHI_{real}}{GHI_{cs}}\times 100")
        st.latex(r"\Delta_{tot} = GHI_{topo} - GHI_{real},\qquad \Delta_{tot}(\%) = \frac{GHI_{topo} - GHI_{real}}{GHI_{topo}}\times 100")
        st.latex(r"\text{Índice relativo} = \frac{GHI_{real}}{GHI_{cs}}\times 100 \qquad \frac{GHI_{real}}{GHI_{topo}} = \frac{GHI_{cs}}{GHI_{topo}}\cdot\frac{GHI_{real}}{GHI_{cs}}")
        st.markdown(
            f"As diferenças mantêm o **sinal**; percentuais só são calculados quando a referência é ≥ {DEN_MIN:.0f} W/m² "
            "(evita divisão por zero ao nascer e ao pôr do sol). A atenuação atmosférica não é descontada duas vezes: "
            "o céu limpo já inclui absorção e espalhamento, e o real vem diretamente da fonte."
        )
