from __future__ import annotations

from pathlib import Path
import re
from typing import Dict, List

import pandas as pd

EQUIPMENT_KEYWORDS = [
    "LOADER",
    "UNLOADER",
    "BUFFER",
    "PECVD",
    "PVD",
    "DRYER",
    "TEST MACHINE",
    "SECTION",
    "FLIPPER",
]

PIPE_SIZE_PATTERN = re.compile(
    r'(\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)\s*"?\s*[øØ]?\s*CDA',
    re.IGNORECASE,
)


def read_plan_text(pdf_path: str | Path) -> str:
    pdf_path = Path(pdf_path)
    parsed_txt = Path(str(pdf_path) + '.parsed.txt')
    if parsed_txt.exists():
        return parsed_txt.read_text(encoding='utf-8', errors='ignore')

    try:
        import fitz
        doc = fitz.open(pdf_path)
        pages = [page.get_text() for page in doc]
        return '\n'.join(pages)
    except Exception:
        return ''


def normalize_size(size_text: str) -> float | None:
    size_text = str(size_text).replace('"', '').replace("'", '').strip()
    size_text = ' '.join(size_text.split())

    if not size_text:
        return None

    try:
        if ' ' in size_text and '/' in size_text:
            whole, frac = size_text.split(' ', 1)
            num, den = frac.split('/')
            return float(whole) + float(num) / float(den)
        if '/' in size_text:
            num, den = size_text.split('/')
            return float(num) / float(den)
        return float(size_text)
    except Exception:
        return None


def extract_pipe_sizes(plan_text: str) -> List[float]:
    sizes = []
    for match in PIPE_SIZE_PATTERN.findall(plan_text):
        try:
            value = normalize_size(match)
            if value is not None and 0 < value <= 24:
                sizes.append(value)
        except Exception:
            continue
    return sorted(set(sizes), reverse=True)


def clean_line(line: str) -> str:
    return ' '.join(str(line).strip().split())


def extract_equipment_lines(plan_text: str) -> List[str]:
    found = []
    for raw_line in plan_text.splitlines():
        line = clean_line(raw_line)
        if not line:
            continue
        upper = line.upper()
        if any(keyword in upper for keyword in EQUIPMENT_KEYWORDS):
            found.append(line)

    deduped = []
    seen = set()
    for line in found:
        key = line.upper()
        if key not in seen:
            seen.add(key)
            deduped.append(line)
    return deduped


def build_draft_tables(
    pdf_path: str | Path,
    template_path: str | Path | None = None,
    default_source_pressure: float = 30.0,
) -> Dict[str, object]:
    plan_text = read_plan_text(pdf_path)
    equipment_lines = extract_equipment_lines(plan_text)
    sizes = extract_pipe_sizes(plan_text)

    if not equipment_lines:
        equipment_lines = [
            'AREA A MAIN DEMAND',
            'AREA B MAIN DEMAND',
        ]

    default_branch_size = sizes[-1] if sizes else 1.0
    default_header_size = sizes[0] if sizes else 4.0

    nodes_rows = []
    segments_rows = []
    loads_rows = []
    sources_rows = []
    warnings = []

    source_node = 'N001'
    nodes_rows.append({
        'Node_ID': source_node,
        'Description': 'AI draft source node',
        'Node_Type': 'Source',
        'X': 0,
        'Y': 0,
        'Active': 'YES',
        'AI_Notes': 'Created automatically as the draft source node.',
        'Needs_Review': 'YES',
    })

    sources_rows.append({
        'Source_ID': 'SRC1',
        'Node_ID': source_node,
        'Pressure_psig': default_source_pressure,
        'Active': 'YES',
        'Description': 'AI draft source',
        'AI_Notes': 'Default source pressure inserted automatically.',
        'Needs_Review': 'YES',
    })

    y_step = 10
    current_y = 0

    for i, equipment in enumerate(equipment_lines, start=2):
        node_id = f'N{i:03d}'
        segment_id = f'P{i-1:03d}'
        load_id = f'L{i-1:03d}'
        current_y -= y_step

        nodes_rows.append({
            'Node_ID': node_id,
            'Description': equipment,
            'Node_Type': 'Demand',
            'X': 20,
            'Y': current_y,
            'Active': 'YES',
            'AI_Notes': 'Created from detected equipment label.',
            'Needs_Review': 'YES',
        })

        segments_rows.append({
            'Segment_ID': segment_id,
            'Type': 'Branch',
            'From_Node': source_node,
            'To_Node': node_id,
            'Description': f'AI draft branch to {equipment}',
            'Size_in': default_branch_size,
            'Length_ft': None,
            'Fittings_ft': 0,
            'Eq_Length_ft': None,
            'Active': 'YES',
            'ID_in': default_branch_size,
            'K_psi_per_scfm2_gas_corrected': None,
            'Max_Vel_Limit_ft_s': 30,
            'AI_Notes': 'Connectivity, length, and K must be reviewed.',
            'Needs_Review': 'YES',
        })

        loads_rows.append({
            'Load_ID': load_id,
            'Equipment': equipment,
            'Node_ID': node_id,
            'Peak_Demand_scfm': None,
            'Active': 'YES',
            'Design_Demand_scfm': None,
            'AI_Notes': 'Demand value must be entered by the user.',
            'Needs_Review': 'YES',
        })

    if sizes:
        warnings.append(
            f"Detected CDA size text in plan: {', '.join(str(s) for s in sizes)} in. These are draft references only."
        )
    else:
        warnings.append('No reliable CDA size text was detected. Default sizes were inserted.')

    warnings.append(
        'Draft topology is a simple source-to-demand star layout. You must review From_Node, To_Node, lengths, demands, and K values.'
    )
    warnings.append(
        'This import step is intentionally conservative. The solver should not run until the draft is reviewed.'
    )

    return {
        'nodes': pd.DataFrame(nodes_rows),
        'segments': pd.DataFrame(segments_rows),
        'loads': pd.DataFrame(loads_rows),
        'sources': pd.DataFrame(sources_rows),
        'warnings': warnings,
        'raw_text_preview': plan_text[:4000],
        'detected_equipment_count': len(equipment_lines),
        'detected_sizes': sizes,
        'default_header_size': default_header_size,
        'default_branch_size': default_branch_size,
    }
