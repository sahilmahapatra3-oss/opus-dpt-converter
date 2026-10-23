
import io
import tempfile
import zipfile
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path

import streamlit as st
from brukeropusreader import read_file


st.set_page_config(
    page_title="Bruker OPUS Absorbance to DPT Converter",
    page_icon="📈",
    layout="centered",
)

st.title("Bruker OPUS → DPT Bulk Converter")

st.write(
    "Upload multiple original Bruker OPUS files. The app extracts the "
    "AB (absorbance) spectrum and its wavenumber axis directly from "
    "each OPUS file, then converts the data into DPT text files."
)

st.info(
    "No reference DPT file is required. The app extracts the absorbance "
    "axis independently from each OPUS file. A file must contain a usable "
    "AB spectrum and matching wavenumber axis to be converted."
)

opus_files = st.file_uploader(
    "1. Select your original OPUS files",
    accept_multiple_files=True,
    help=(
        "Select multiple original Bruker OPUS binary files. "
        "Files may have extensions such as .0 or .0001. "
        "Do not upload exported DPT or CSV files."
    ),
)


def unique_output_name(original_name, counts):
    """Create unique output filenames while preserving recognizable names."""
    base = Path(original_name.replace("\\", "/")).name or "opus_file"

    counts[base] = counts.get(base, 0) + 1

    if counts[base] == 1:
        return base + ".dpt"

    return f"{base}_{counts[base]}.dpt"


if st.button(
    "Convert OPUS absorbance files to DPT",
    type="primary",
    disabled=not opus_files,
):
    results = []
    failures = []
    name_counts = {}

    progress = st.progress(0, text="Preparing files...")

    with tempfile.TemporaryDirectory(prefix="opus_dpt_") as temp_dir:
        total = len(opus_files)

        for index, uploaded in enumerate(opus_files, start=1):
            original_name = (
                Path(uploaded.name.replace("\\", "/")).name
                or f"opus_file_{index}"
            )

            input_path = (
                Path(temp_dir) / f"input_{index}_{original_name}"
            )

            try:
                # Save the uploaded OPUS binary temporarily.
                input_path.write_bytes(uploaded.getvalue())

                # Read the OPUS file.
                opus_data = read_file(str(input_path))

                # Verify that the absorbance block exists.
                if "AB" not in opus_data:
                    available = ", ".join(
                        map(str, list(opus_data.keys())[:25])
                    )

                    raise ValueError(
                        "This OPUS file does not contain an AB "
                        "(absorbance) spectrum. "
                        f"Available fields: {available or 'none'}. "
                        "Check that absorbance data was acquired or "
                        "saved in this OPUS file."
                    )

                # Extract the absorbance wavenumber axis.
                try:
                    x_values = opus_data.get_range("AB")
                except Exception as exc:
                    raise ValueError(
                        f"Could not extract the AB wavenumber axis: {exc}"
                    ) from exc

                if x_values is None or len(x_values) == 0:
                    raise ValueError(
                        "The OPUS reader returned an empty AB wavenumber axis."
                    )

                # Extract absorbance intensities.
                y_raw = opus_data["AB"]

                try:
                    y_count = len(y_raw)
                except TypeError as exc:
                    raise ValueError(
                        "The AB absorbance data is not a usable sequence."
                    ) from exc

                # Ensure sufficient intensity values are available.
                if y_count < len(x_values):
                    raise ValueError(
                        f"Axis/data length mismatch: the AB wavenumber "
                        f"axis contains {len(x_values)} points, but the "
                        f"absorbance block contains {y_count} values."
                    )

                # AB can have extra trailing values; retain only the
                # intensity values corresponding to the wavenumber axis.
                y_values = y_raw[:len(x_values)]

                if len(y_values) != len(x_values):
                    raise ValueError(
                        "Could not align AB intensities with the "
                        "wavenumber axis."
                    )

                # Check that the axis contains numeric values.
                try:
                    for x_value in x_values:
                        float(x_value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        "The extracted AB wavenumber axis contains "
                        "non-numeric values."
                    ) from exc

                # Export wavenumber, absorbance pairs.
                # Preserve five decimal places and half-even rounding.
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

            except Exception as exc:
                failures.append((original_name, str(exc)))

            progress.progress(
                index / total,
                text=f"Processed {index} of {total} file(s)...",
            )

    st.subheader("Conversion report")

    col1, col2 = st.columns(2)
    col1.metric("Converted successfully", len(results))
    col2.metric("Failed", len(failures))

    if results:
        zip_buffer = io.BytesIO()

        with zipfile.ZipFile(
            zip_buffer,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
        ) as zip_file:
            for output_name, output_bytes in results:
                zip_file.writestr(output_name, output_bytes)

            if failures:
                report = (
                    "Files that failed conversion\n\n"
                    + "\n".join(
                        f"{name}: {reason}"
                        for name, reason in failures
                    )
                )

                zip_file.writestr(
                    "conversion_errors.txt",
                    report,
                )

        st.download_button(
            "Download converted absorbance DPT files (ZIP)",
            data=zip_buffer.getvalue(),
            file_name="converted_absorbance_dpt_files.zip",
            mime="application/zip",
            type="primary",
        )

        st.success(
            "Conversion completed. Compare at least one result with "
            "an absorbance DPT exported directly from OPUS before "
            "using these files for research."
        )

    else:
        st.error(
            "No files were converted successfully. "
            "Expand the failure report below."
        )

    if failures:
        with st.expander(
            "Show failed files and reasons",
            expanded=True,
        ):
            for name, reason in failures:
                st.write(f"**{name}** — {reason}")

st.divider()

st.caption(
    "The AB absorbance spectrum and its wavenumber axis are extracted "
    "directly from each OPUS file. Intensities are exported to five "
    "decimal places using ROUND_HALF_EVEN rounding. Validate the "
    "output against OPUS before quantitative analysis."
)
