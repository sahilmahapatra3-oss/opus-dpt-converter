"""
Bruker OPUS -> DPT / CSV converter  (Streamlit app, single file)
Needs only: streamlit, numpy, pandas      (brukeropusreader is NOT used)
Run:  streamlit run app.py
"""
import io, zipfile
from pathlib import Path
import numpy as np
import pandas as pd
import streamlit as st
import re, struct, math, datetime

MAGIC = bytes.fromhex('0a0afefe')


def is_opus(data):
    return len(data) > 24 and data[:4] == MAGIC


def _read_dir(b):
    ptr, maxn, curn = struct.unpack('<iii', b[12:24])
    ents = {}
    for i in range(curn):
        o = ptr + 12 * i
        if o + 12 > len(b):
            break
        t0, t1, t2, t3, words, off = struct.unpack('<4BII', b[o:o + 12])
        if words and off + words * 4 <= len(b):
            ents[(t0, t1, t2, t3)] = (words, off)
    return ents


def _params(b, off, words):
    end, o, P = off + words * 4, off, {}
    while o + 8 <= end:
        name = b[o:o + 3].decode('latin1')
        if name.startswith('END') or not name.strip('\x00'):
            break
        ty, sz = struct.unpack('<hh', b[o + 4:o + 8])
        raw = b[o + 8:o + 8 + 2 * sz]
        try:
            if ty == 0:
                v = struct.unpack('<i', raw[:4])[0]
            elif ty == 1:
                v = struct.unpack('<d', raw[:8])[0]
            else:
                v = raw.split(b'\x00')[0].decode('latin1')
        except Exception:
            v = None
        P[name] = v
        o += 8 + 2 * sz
    return P


def parse_opus(data):
    """Return list of spectral data blocks: dict(key, nu, y, P). Axis = FXV..LXV."""
    if not is_opus(data):
        raise ValueError('not an OPUS file (magic number 0A0AFEFE missing)')
    ents = _read_dir(data)
    out = []
    for key, (words, off) in ents.items():
        t0 = key[0]
        if t0 == 0 or t0 >= 16:            # parameter / text / directory blocks
            continue
        pk = (t0 + 16,) + key[1:]          # its "data status" parameter block
        if pk not in ents:
            continue
        P = _params(data, ents[pk][1], ents[pk][0])
        n = P.get('NPT')
        if not isinstance(n, int) or n < 10 or n * 4 > words * 4:
            continue
        if 'FXV' not in P or 'LXV' not in P:
            continue
        y = np.frombuffer(data[off:off + 4 * n], dtype='<f4').astype(float) * float(P.get('CSF', 1.0) or 1.0)
        nu = np.linspace(P['FXV'], P['LXV'], n)
        out.append(dict(key=key[:2], nu=nu, y=y, P=P))
    return out


def _interp(nu_to, blk):
    nu, y = blk['nu'], blk['y']
    if nu[0] > nu[-1]:
        nu, y = nu[::-1], y[::-1]
    return np.interp(nu_to, nu, y)


def classify(blocks):
    """Decide which block is sample / reference / TR / AB from the numbers."""
    roles, notes = {}, []
    n = len(blocks)
    for ri in range(n):
        R = blocks[ri]
        for xi in range(n):
            for yi in range(n):
                if len({ri, xi, yi}) < 3:
                    continue
                X = _interp(R['nu'], blocks[xi]); Y = _interp(R['nu'], blocks[yi])
                m = (X > 0.05 * X.max()) & (Y > 0.05 * Y.max()) & (X > 0) & (Y > 0)
                if m.sum() < 30:
                    continue
                ratio = X[m] / Y[m]
                r = R['y'][m]
                q = np.median(r / ratio)
                if abs(q - 1) < 0.03 and np.mean(np.abs(r / ratio - q) < 0.03 * q) > 0.6:
                    kind = 'TR'
                elif abs(q - 100) < 3 and np.mean(np.abs(r / ratio - q) < 0.03 * q) > 0.6:
                    kind = 'TR_percent'
                elif np.median(np.abs(r + np.log10(ratio))) < 0.02 and np.mean(np.abs(r + np.log10(ratio)) < 0.03) > 0.6:
                    kind = 'AB'
                else:
                    continue
                if kind not in roles:
                    roles[kind] = ri
                if 'sample' not in roles:
                    roles['sample'], roles['reference'] = xi, yi
                    notes.append('sample/reference identified because the result block equals sample/reference')
    if 'sample' not in roles:
        d = {b['key'][0]: i for i, b in enumerate(blocks)}
        if 7 in d and 11 in d:
            roles['sample'], roles['reference'] = d[7], d[11]
            notes.append('sample/reference ASSUMED from OPUS block codes 7 / 11 (no result block to verify)')
    return roles, notes


def parse_time(P):
    """seconds on a common clock from DAT (dd/mm/yyyy) and TIM (hh:mm:ss.sss ...)"""
    try:
        d = P.get('DAT', '')
        t = re.match(r'(\d+):(\d+):([\d.]+)', P.get('TIM', ''))
        h, mi, s = int(t.group(1)), int(t.group(2)), float(t.group(3))
        day = 0
        m = re.match(r'(\d+)[/.-](\d+)[/.-](\d+)', d)
        if m:
            a, b_, c = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if a > 31:
                y_, mo_, d_ = a, b_, c
            else:
                d_, mo_, y_ = a, b_, c
            day = datetime.date(y_, mo_, d_).toordinal()
        return day * 86400 + h * 3600 + mi * 60 + s
    except Exception:
        return float('nan')


def load_spectrum(name, data):
    """Parse one file -> dict with blocks, roles and notes."""
    blocks = parse_opus(data)
    if not blocks:
        raise ValueError('no spectral data blocks found')
    roles, notes = classify(blocks)
    S = dict(name=name, blocks=blocks, roles=roles, notes=notes)
    ref_blk = None
    for k in ('TR', 'TR_percent', 'AB', 'sample'):
        if k in roles:
            ref_blk = blocks[roles[k]]
            break
    S['time'] = parse_time((ref_blk or blocks[0])['P'])
    S['codes'] = [b['key'] for b in blocks]
    return S


QUANTITIES = {
    'Transmittance, fraction (0-1)': 'TR',
    'Transmittance, percent (%T)': 'TRP',
    'Absorbance (AB)': 'AB',
    'Single-channel SAMPLE (ScSm)': 'SM',
    'Single-channel REFERENCE (ScRf)': 'RF',
}


def get_quantity(S, q):
    """return (nu, y, source_note). Never applies 10^-A to a block that is not verified AB."""
    B, R = S['blocks'], S['roles']

    def ratio():
        if 'sample' in R and 'reference' in R:
            s, r = B[R['sample']], B[R['reference']]
            y = s['y'] / np.clip(_interp(s['nu'], r), 1e-30, None)
            return s['nu'], y
        return None

    if q in ('TR', 'TRP'):
        f = 1.0 if q == 'TR' else 100.0
        if 'TR' in R:
            b = B[R['TR']]; return b['nu'], b['y'] * f, 'stored transmittance block (verified = sample/reference)'
        if 'TR_percent' in R:
            b = B[R['TR_percent']]; return b['nu'], b['y'] / 100 * f, 'stored %T block (verified), converted'
        rr = ratio()
        if rr is not None:
            return rr[0], rr[1] * f, 'calculated sample / reference'
        if 'AB' in R:
            b = B[R['AB']]; return b['nu'], 10 ** (-b['y']) * f, 'calculated from verified AB block, T = 10^(-A)'
        raise ValueError('cannot obtain transmittance from this file')
    if q == 'AB':
        if 'AB' in R:
            b = B[R['AB']]; return b['nu'], b['y'], 'stored absorbance block (verified)'
        nu, t, src = get_quantity(S, 'TR')
        return nu, -np.log10(np.clip(t, 1e-9, None)), 'calculated A = -log10(T) from ' + src
    if q in ('SM', 'RF'):
        k = 'sample' if q == 'SM' else 'reference'
        if k not in R:
            raise ValueError('no %s single-channel block identified' % k)
        b = B[R[k]]; return b['nu'], b['y'], 'single-channel block'
    raise ValueError('unknown quantity ' + q)


def crop_order(nu, y, lo=None, hi=None, order='as stored'):
    m = np.ones(nu.size, bool)
    if lo is not None: m &= nu >= lo
    if hi is not None: m &= nu <= hi
    nu, y = nu[m], y[m]
    if order == 'ascending' and nu[0] > nu[-1]:
        nu, y = nu[::-1], y[::-1]
    if order == 'descending' and nu[0] < nu[-1]:
        nu, y = nu[::-1], y[::-1]
    return nu, y


def dpt_text(nu, y, sig=10):
    fmt = '%%.%dg' % sig
    return ''.join('%s,%s\n' % (fmt % a, fmt % b) for a, b in zip(nu, y))


def matrix_text(nu_ref, cols, xvals, sig=10):
    """CSV for the Octave scripts: row 1 = [0, x1, x2, ...]; rows = [nu, y1, y2, ...]"""
    fmt = '%%.%dg' % sig
    asc = nu_ref[0] < nu_ref[-1]
    lines = [','.join(['0'] + [fmt % x for x in xvals])]
    for i, a in enumerate(nu_ref):
        lines.append(','.join([fmt % a] + [fmt % c[i] for c in cols]))
    return '\n'.join(lines) + '\n'


def parse_conc(name):
    base = name.replace('\\', '/').split('/')[-1]
    m = re.match(r'^(\d+)-(\d+)\s*M', base, re.I)
    if m:
        return float(m.group(1) + '.' + m.group(2))
    m = re.match(r'^(\d+(?:[.,]\d+)?)\s*M', base, re.I)
    return float(m.group(1).replace(',', '.')) if m else float('nan')


# =====================================================================
# Streamlit user interface
# =====================================================================
st.set_page_config(page_title="OPUS converter", page_icon="📈", layout="wide")
st.title("Bruker OPUS → DPT / CSV converter")
st.caption("Reads the OPUS binary directly. The role of every block (sample, reference, "
           "transmittance, absorbance) is checked from the numbers, so a mislabelled block "
           "cannot be silently converted wrongly.")

# ---------------------------------------------------------------- upload
files = st.file_uploader(
    "1. Upload the original OPUS files (several files, or one .zip containing them)",
    accept_multiple_files=True)


@st.cache_data(show_spinner=False)
def parse_cached(name, data):
    return load_spectrum(name, data)


items, skipped = [], []


def add_file(name, data):
    if is_opus(data):
        items.append((Path(name).name, data))
    else:
        skipped.append(Path(name).name)


for f in files or []:
    raw = f.getvalue()
    if f.name.lower().endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            for m in z.namelist():
                if not m.endswith("/"):
                    add_file(m, z.read(m))
    else:
        add_file(f.name, raw)

if skipped:
    st.warning("Ignored (not OPUS binary files): " + ", ".join(skipped[:15]) + (" ..." if len(skipped) > 15 else ""))
if not items:
    st.info("Upload OPUS files to start.")
    st.stop()

specs, bad = [], []
for name, data in items:
    try:
        specs.append(parse_cached(name, data))
    except Exception as e:
        bad.append((name, str(e)))
for n, e in bad:
    st.error(f"{n}: {e}")
if not specs:
    st.stop()

# ---------------------------------------------------------------- options
st.subheader("2. Options")
c1, c2, c3 = st.columns(3)
with c1:
    qlabels = st.multiselect("Spectrum type(s) to export", list(QUANTITIES.keys()),
                             default=["Transmittance, fraction (0-1)"])
    layout = st.multiselect("Output layout", [
        "One file per spectrum (ZIP)",
        "Matrix CSV: time series (for run_cavity_kinetics)",
        "Matrix CSV: concentration series (calibration)"],
        default=["One file per spectrum (ZIP)"])
with c2:
    ext = st.text_input("Extension for single files", ".dpt")
    delim_name = st.selectbox("Column separator (single files)", ["comma", "tab", "space"])
    sig = st.number_input("Significant digits", min_value=5, max_value=17, value=10,
                          help="Low-transmission cavity data need many digits. Never round to 5 decimals.")
with c3:
    order = st.selectbox("Wavenumber order", ["as stored", "ascending", "descending"])
    lo = st.number_input("Min wavenumber (0 = no limit)", value=0.0)
    hi = st.number_input("Max wavenumber (0 = no limit)", value=0.0)
    lo = lo or None
    hi = hi or None

excl = st.text_input("Skip files whose name contains (comma separated, case-insensitive)", "empty")
excl_list = [e.strip().lower() for e in excl.split(",") if e.strip()]

# ---------------------------------------------------------------- file table
t0 = min([s["time"] for s in specs if s["time"] == s["time"]] or [0.0])
rows = []
for s in specs:
    roles = s["roles"]
    res = "TR" if "TR" in roles else "TR(%)" if "TR_percent" in roles else "AB" if "AB" in roles else "-"
    b = s["blocks"][0]
    rows.append(dict(include=not any(e in s["name"].lower() for e in excl_list),
                     file=s["name"], time_s=round(s["time"] - t0, 2),
                     result_block=res, block_codes=str(s["codes"]),
                     points=len(b["nu"]), wn_range=f'{b["nu"].min():.1f}-{b["nu"].max():.1f}'))
df = pd.DataFrame(rows)
st.write("**Files found** - untick `include` to leave one out. `result_block` is what the stored "
         "result really is (checked numerically).")
df = st.data_editor(df, hide_index=True, disabled=[c for c in df.columns if c != "include"])
sel = [s for s, inc in zip(specs, df["include"].tolist()) if inc]
if not sel:
    st.warning("No file selected.")
    st.stop()
kinds = {tuple(sorted(s["roles"].keys())) for s in sel}
if len(kinds) > 1:
    st.warning("The selected files do not all contain the same kinds of blocks: " + str(kinds))
for n in sorted({n for s in sel for n in s["notes"]}):
    st.caption("note: " + n)

# time / concentration settings
tcol, ccol = st.columns(2)
with tcol:
    dedupe = st.checkbox("Time series: drop repeated records (same time stamp AND same data)", True)
    offset = st.number_input("Time series: time of the first kept spectrum (s)", value=0.0,
                             help="0 = start the clock at the first spectrum you measured. "
                                  "Enter a delay (s) if mixing happened earlier.")
conc_df = None
if any("Matrix CSV: concentration" in x for x in layout):
    with ccol:
        st.write("Concentrations (read from file names such as 0-3M.0 = 0.3 M; edit if wrong)")
        conc_df = st.data_editor(pd.DataFrame(dict(file=[s["name"] for s in sel],
                                                   conc_M=[parse_conc(s["name"]) for s in sel])),
                                 hide_index=True, disabled=["file"], key="conc")

# ---------------------------------------------------------------- convert
if st.button("Convert", type="primary"):
    if not qlabels or not layout:
        st.error("Choose at least one spectrum type and one output layout.")
        st.stop()
    sep = {"comma": ",", "tab": "\t", "space": " "}[delim_name]
    buf, report, nfiles = io.BytesIO(), [], 0
    report.append("OPUS converter report")
    report.append(f"files used: {len(sel)}   significant digits: {int(sig)}")
    multi = len(qlabels) > 1
    prev = None
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for ql in qlabels:
            q = QUANTITIES[ql]
            data = []
            for s in sel:
                try:
                    nu, y, src = get_quantity(s, q)
                    nu, y = crop_order(nu, y, lo, hi, order)
                    data.append((s, nu, y, src))
                except Exception as e:
                    report.append(f"FAILED {s['name']} [{q}]: {e}")
            if not data:
                continue
            report.append(f"\n[{ql}]")
            for s, nu, y, src in data[:3]:
                report.append(f"  e.g. {s['name']}: {src}")
            if "One file per spectrum (ZIP)" in layout:
                for s, nu, y, src in data:
                    text = dpt_text(nu, y, int(sig))
                    if sep != ",":
                        text = text.replace(",", sep)
                    z.writestr((f"{q}/" if multi else "") + s["name"] + ext, text)
                    nfiles += 1
            want_t = "Matrix CSV: time series (for run_cavity_kinetics)" in layout
            want_c = "Matrix CSV: concentration series (calibration)" in layout
            if want_t or want_c:
                nu_ref = data[0][1]
                def res(nu, y):
                    a, b = (nu, y) if nu[0] < nu[-1] else (nu[::-1], y[::-1])
                    r = np.interp(nu_ref if nu_ref[0] < nu_ref[-1] else nu_ref[::-1], a, b)
                    return r if nu_ref[0] < nu_ref[-1] else r[::-1]
            if want_t:
                d2 = sorted(data, key=lambda r: r[0]["time"])
                keep, last = [], None
                for r in d2:
                    ycur = res(r[1], r[2])
                    if dedupe and last is not None and abs(r[0]["time"] - last[0]["time"]) < 1.0 \
                            and np.allclose(ycur, last[1], rtol=1e-9, atol=1e-12):
                        report.append(f"  dropped repeated record: {r[0]['name']}")
                        continue
                    keep.append((r, ycur)); last = (r[0], ycur)
                x = [r[0]["time"] - keep[0][0][0]["time"] + offset for r, _ in keep]
                z.writestr("cavity_spectra" + (f"_{q}" if multi else "") + ".csv",
                           matrix_text(nu_ref, [c for _, c in keep], x, int(sig)))
                report.append(f"  time-series matrix: {len(keep)} spectra, times {x[0]:.1f} .. {x[-1]:.1f} s")
            if want_c and conc_df is not None:
                cm = dict(zip(conc_df["file"], conc_df["conc_M"]))
                d3 = [(cm[r[0]["name"]], res(r[1], r[2])) for r in data if cm.get(r[0]["name"]) == cm.get(r[0]["name"])]
                d3.sort(key=lambda t: t[0])
                if d3:
                    z.writestr("calib_spectra" + (f"_{q}" if multi else "") + ".csv",
                               matrix_text(nu_ref, [c for _, c in d3], [c for c, _ in d3], int(sig)))
                    report.append(f"  concentration matrix: {len(d3)} spectra")
            prev = data
        z.writestr("conversion_report.txt", "\n".join(report))
    pv = None
    if prev:
        pv = pd.DataFrame({prev[0][0]["name"]: pd.Series(prev[0][2], index=prev[0][1]),
                           prev[-1][0]["name"]: pd.Series(prev[-1][2], index=prev[-1][1])})
    st.session_state["result"] = dict(zip=buf.getvalue(), report=report, preview=pv)

# results stay on screen after the download button is clicked
res_ = st.session_state.get("result")
if res_:
    st.success("Done - download the ZIP below.")
    st.text("\n".join(res_["report"][:60]))
    st.download_button("Download results (ZIP)", res_["zip"], "opus_converted.zip", "application/zip")
    if res_["preview"] is not None:
        st.write("Preview: first and last spectrum of the last exported type")
        st.line_chart(res_["preview"])
