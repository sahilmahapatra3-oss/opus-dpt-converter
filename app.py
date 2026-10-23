
import io
import math
import tempfile
import zipfile
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path

import streamlit as st
from brukeropusreader import read_file


st.set_page_config(
    page_title="Bruker OPUS to DPT Converter",
    page_icon="📈",
    layout="centered",
)

st.title("Bruker OPUS → DPT Bulk Converter")

spectrum_options = {
    "Transmittance (TR)": "TR",
    "Absorbance (AB)": "AB",
}

spectrum_label = st.selectbox(
    "1. Select the spectrum you want to export",
    options=list(spectrum_options.keys()),
    index=0,
)

spectrum_code = spectrum_options[spectrum_label]

st.write(
    f"Upload multiple original Bruker OPUS files. "
    f"The app extracts the selected {spectrum_label} spectrum "
    "and its wavenumber axis from each file."
)

if spectrum_code == "TR":
    st.info(
        "Transmittance mode: the app first looks for a stored TR block. "
        "If TR is unavailable but AB exists, it calculates fractional "
        "transmittance using T = 10^(-A). Calculated values are on a "
        "0–1 scale, not a percentage scale. Validate the results against "
        "a transmittance spectrum exported directly from OPUS."
    )
else:
    st.info(
        "Absorbance mode: the app extracts the AB spectrum and its "
        "wavenumber axis directly from each OPUS file. No reference "
        "DPT file is required."
    )

opus_files = st.file_uploader(
    "2. Select your original OPUS files",
    accept_multiple_files=True,
    help=(
        "Upload multiple original Bruker OPUS binary files, including "
        "files with extensions such as .0 or .0001. Do not upload "
        "previously exported DPT or CSV files."
    ),
)


def unique_output_name(original_name, counts):
    """Create unique filenames without losing the original file identity."""
    base = Path(original_name.replace("\\", "/")).name or "opus_file"

    counts[base] = counts.get(base, 0) + 1

    if counts[base] == 1:
        return base + ".dpt"

    return f"{base}_{counts[base]}.dpt"


if st.button(
    f"Convert {spectrum_label} files to DPT",
    type="primary",
    disabled=not opus_files,
):
    results = []
    failures = []
    name_counts = {}

    direct_tr_count = 0
    calculated_tr_count = 0
    direct_ab_count = 0

    progress = st.progress(0, text="Preparing files...")

    with tempfile.TemporaryDirectory(prefix="opus_dpt_") as temp_dir:
        total = len(opus_files)

        for index, uploaded in enumerate(opus_files, start=1):
            original_name = (
                Path(uploaded.name.replace("\\", "/")).name
                or f"opus_file_{index}"
            )

            input_path = Path(temp_dir) / f"input_{index}_{original_name}"

            try:
                # Save the uploaded OPUS binary temporarily.
                input_path.write_bytes(uploaded.getvalue())

                # Read the original OPUS file.
                opus_data = read_file(str(input_path))

                used_ab_conversion = False

                # Select the appropriate source data block.
                if spectrum_code == "TR":
                    if "TR" in opus_data:
                        source_code = "TR"

                    elif "AB" in opus_data:
                        # TR is not saved, so derive it from absorbance.
                        source_code = "AB"
                        used_ab_conversion = True

                    else:
                        available = ", ".join(
                            map(str, list(opus_data.keys())[:25])
                        )

                        raise ValueError(
                            "Neither a TR nor an AB spectrum is available. "
                            f"Available fields: {available or 'none'}."
                        )

                else:
                    source_code = "AB"

                    if source_code not in opus_data:
                        available = ", ".join(
                            map(str, list(opus_data.keys())[:25])
                        )

                        raise ValueError(
                            "This OPUS file does not contain an AB "
                            f"absorbance spectrum. Available fields: "
                            f"{available or 'none'}."
                        )

                # Extract the wavenumber axis associated with this block.
                try:
                    x_values = opus_data.get_range(source_code)
                except Exception as exc:
                    raise ValueError(
                        f"Could not extract the {source_code} "
                        f"wavenumber axis: {exc}"
                    ) from exc

                if x_values is None or len(x_values) == 0:
                    raise ValueError(
                        f"The {source_code} wavenumber axis is empty."
                    )

                # Extract the spectral intensity values.
                y_raw = opus_data[source_code]

                try:
                    y_count = len(y_raw)
                except TypeError as exc:
                    raise ValueError(
                        f"The {source_code} spectrum is not a usable sequence."
                    ) from exc

                if y_count < len(x_values):
                    raise ValueError(
                        f"Axis/data mismatch: the axis contains "
                        f"{len(x_values)} points, but {source_code} "
                        f"contains only {y_count} intensity values."
                    )

                # Match the intensity array to the axis length.
                x_values = [float(value) for value in x_values]
                y_values = [
                    float(value) for value in y_raw[:len(x_values)]
                ]

                if len(y_values) != len(x_values):
                    raise ValueError(
                        "Could not align the spectral values with the "
                        "wavenumber axis."
                    )

                # Reject invalid numeric data.
                if not all(math.isfinite(value) for value in x_values):
                    raise ValueError(
                        "The wavenumber axis contains invalid numeric values."
                    )

                if not all(math.isfinite(value) for value in y_values):
                    raise ValueError(
                        f"The {source_code} spectrum contains invalid "
                        "numeric values."
                    )

                # Calculate fractional transmittance when TR is unavailable.
                # Bruker AB-to-TR relation: T = 10^(-A).
                if used_ab_conversion:
                    try:
                        y_values = [
                            math.pow(10.0, -value)
                            for value in y_values
                        ]
                    except (OverflowError, ValueError) as exc:
                        raise ValueError(
                            "Could not calculate transmittance from AB."
                        ) from exc

                    if not all(
                        math.isfinite(value) for value in y_values
                    ):
                        raise ValueError(
                            "The calculated transmittance contains "
                            "invalid numeric values."
                        )

                # Export wavenumber,intensity pairs.
                # Preserve five-decimal ROUND_HALF_EVEN rounding.
                output_lines = []

                for x_value, y_value in zip(x_values, y_values):
                    y_rounded = Decimal(str(y_value)).quantize(
                        Decimal("0.00001"),
                        rounding=ROUND_HALF_EVEN,
                    )

                    output_lines.append(
                        f"{x_value},{y_rounded}\n"
                    )

                output_bytes = "".join(output_lines).encode("utf-8")

                output_name = unique_output_name(
                    original_name,
                    name_counts,
                )

                results.append((output_name, output_bytes))

                if spectrum_code == "TR":
                    if used_ab_conversion:
                        calculated_tr_count += 1
                    else:
                        direct_tr_count += 1
                else:
                    direct_ab_count += 1

            except Exception as exc:
                failures.append((original_name, str(exc)))

            progress.progress(
                index / total,
                text=f"Processed {index} of {total} file(s)...",
            )

    # Display conversion summary.
    st.subheader("Conversion report")

    col1, col2 = st.columns(2)
    col1.metric("Converted successfully", len(results))
    col2.metric("Failed", len(failures))

    if spectrum_code == "TR":
        st.write(f"Exported from stored TR blocks: {direct_tr_count}")
        st.write(f"Calculated from AB blocks: {calculated_tr_count}")

    if results:
        zip_buffer = io.BytesIO()

        with zipfile.ZipFile(
            zip_buffer,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
        ) as zip_file:
            for output_name, output_bytes in results:
                zip_file.writestr(output_name, output_bytes)

            # Include a report documenting how each file was processed.
            report_lines = [
                f"Requested output: {spectrum_label}",
                "",
                f"Successfully converted: {len(results)}",
                f"Failed: {len(failures)}",
            ]

            if spectrum_code == "TR":
                report_lines.extend([
                    f"Exported directly from TR: {direct_tr_count}",
                    f"Calculated from AB: {calculated_tr_count}",
                    "Calculated transmittance scale: fraction (0–1)",
                    "Formula used for AB fallback: T = 10^(-A)",
                ])

            if failures:
                report_lines.append("")
                report_lines.append("Files that failed conversion:")

                report_lines.extend(
                    f"{name}: {reason}"
                    for name, reason in failures
                )

            zip_file.writestr(
                "conversion_report.txt",
                "\n".join(report_lines),
            )

        output_suffix = spectrum_code.lower()

        st.download_button(
            f"Download {spectrum_label} DPT files (ZIP)",
            data=zip_buffer.getvalue(),
            file_name=f"converted_{output_suffix}_dpt_files.zip",
            mime="application/zip",
            type="primary",
        )

        st.success(
            "The ZIP file is ready. Validate at least one converted "
            "spectrum against an export from OPUS before using it "
            "for quantitative analysis."
        )

    else:
        st.error(
            "No files were converted successfully. "
            "Expand the failure report below."
        )

    if failures:
        with st.expander("Show failed files and reasons", expanded=True):
            for name, reason in failures:
                st.write(f"**{name}** — {reason}")

st.divider()

st.caption(
    "The application extracts each spectrum's own wavenumber axis. "
    "Intensity values are exported to five decimal places using "
    "ROUND_HALF_EVEN rounding. When TR is absent, transmittance is "
    "calculated from AB and is expressed as a fraction, not percent. "
    "Validate the output against OPUS before research use."
)
