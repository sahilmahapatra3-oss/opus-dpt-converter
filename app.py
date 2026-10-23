import io
import os
import tempfile
import zipfile
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path

import streamlit as st
from brukeropusreader import read_file

st.set_page_config(page_title="Bruker OPUS → DPT Converter", page_icon="📈", layout="centered")

st.title("Bruker OPUS → DPT Bulk Converter")
st.write(
    "Upload multiple Bruker OPUS files and convert their **ScSm (single-channel sample spectrum)** "
    "data to DPT text files. This web app follows the extraction and rounding approach of the "
    "original GitHub script."
)

st.warning(
    "The original script takes the wavenumber axis from a manually exported DPT template. "
    "Upload a template exported from OPUS that has the same axis/range/number of points as the files "
    "you are converting. The app checks that the number of X and Y values matches; it cannot prove "
    "the template axis matches the uploaded OPUS files scientifically."
)

opus_files = st.file_uploader(
    "1. Select OPUS binary files (you can select multiple files)",
    accept_multiple_files=True,
    help="Upload the original binary OPUS files, including files with extensions such as .0 or .0001. Do not upload exported CSV/DPT files here.",
)
template_file = st.file_uploader(
    "2. Upload a reference .DPT template exported from OPUS",
    accept_multiple_files=False,
    help="The template must contain one wavenumber,intensity pair per line, like the original converter expects.",
)


def read_x_axis(template_bytes: bytes):
    text = template_bytes.decode("utf-8-sig", errors="replace")
    x_values = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        if "," not in line:
            raise ValueError(
                f"The reference DPT does not look like comma-separated X,Y data (line {line_number}). "
                "Export a standard DPT from OPUS containing wavenumber,intensity on each line."
            )
        x_text = line.split(",", 1)[0].strip()
        try:
            float(x_text)
        except ValueError as exc:
            raise ValueError(
                f"Could not read the wavenumber in the reference DPT at line {line_number}: {x_text!r}. "
                "Use a numeric DPT file without a column-header row."
            ) from exc
        x_values.append(x_text)
    if not x_values:
        raise ValueError("The reference DPT contains no usable wavenumber values.")
    return x_values


def unique_output_name(original_name: str, counts: dict[str, int]) -> str:
    # Avoid folder traversal and keep the uploaded file's recognizable name.
    base = Path(original_name.replace("\\", "/")).name or "opus_file"
    counts[base] = counts.get(base, 0) + 1
    suffix = ".dpt"
    candidate = base + suffix
    if counts[base] > 1:
        candidate = f"{base}_{counts[base]}{suffix}"
    return candidate


if st.button("Convert OPUS files to DPT", type="primary", disabled=not opus_files or not template_file):
    try:
        x_values = read_x_axis(template_file.getvalue())
    except Exception as exc:
        st.error(f"Could not read the reference DPT: {exc}")
        st.stop()

    results = []
    failures = []
    name_counts = {}
    progress = st.progress(0, text="Preparing files…")

    with tempfile.TemporaryDirectory(prefix="opus_dpt_") as temp_dir:
        total = len(opus_files)
        for index, uploaded in enumerate(opus_files, start=1):
            original_name = Path(uploaded.name.replace("\\", "/")).name or f"opus_file_{index}"
            input_path = Path(temp_dir) / f"input_{index}_{original_name}"
            try:
                input_path.write_bytes(uploaded.getvalue())
                opus_data = read_file(str(input_path))

                if "ScSm" not in opus_data:
                    available = ", ".join(map(str, list(opus_data.keys())[:25]))
                    raise KeyError(
                        "This OPUS file has no 'ScSm' spectrum. "
                        f"Fields found (first 25): {available or 'none'}"
                    )

                y_values = opus_data["ScSm"]
                try:
                    y_count = len(y_values)
                except TypeError as exc:
                    raise ValueError("The ScSm data block is not a one-dimensional sequence.") from exc

                if len(x_values) != y_count:
                    raise ValueError(
                        f"Axis length mismatch: template has {len(x_values)} wavenumbers, "
                        f"but ScSm contains {y_count} intensity values. No DPT was written for this file."
                    )

                output_name = unique_output_name(original_name, name_counts)
                output_lines = []
                for x_value, y_value in zip(x_values, y_values):
                    y_rounded = Decimal(str(y_value)).quantize(
                        Decimal(".00001"), rounding=ROUND_HALF_EVEN
                    )
                    output_lines.append(f"{x_value},{y_rounded}\n")
                output_bytes = "".join(output_lines).encode("utf-8")
                results.append((output_name, output_bytes))
            except Exception as exc:
                failures.append((original_name, str(exc)))
            progress.progress(index / total, text=f"Processed {index} of {total} file(s)…")

    st.subheader("Conversion report")
    col1, col2 = st.columns(2)
    col1.metric("Converted successfully", len(results))
    col2.metric("Failed", len(failures))

    if results:
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zip_file:
            for output_name, output_bytes in results:
                zip_file.writestr(output_name, output_bytes)
            if failures:
                report = "Files that failed conversion\n\n" + "\n".join(
                    f"{name}: {reason}" for name, reason in failures
                )
                zip_file.writestr("conversion_errors.txt", report)
        st.download_button(
            "Download converted DPT files (ZIP)",
            data=zip_buffer.getvalue(),
            file_name="converted_dpt_files.zip",
            mime="application/zip",
            type="primary",
        )
        st.success("The ZIP is ready. Validate at least one result against a DPT exported directly from OPUS before using it for research.")
    else:
        st.error("No files were converted successfully. Read the failure messages below.")

    if failures:
        with st.expander("Show failed files and reasons", expanded=True):
            for name, reason in failures:
                st.write(f"**{name}** — {reason}")

st.divider()
st.caption(
    "Scientific validation note: the converter preserves the original script's ScSm extraction and "
    "5-decimal ROUND_HALF_EVEN formatting. A matching reference DPT axis is still required."
)
