import os
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from cross_hardy_pipe_tool_plan_import import build_draft_tables
from cross_hardy_pipe_tool_solver import (
    export_reviewed_draft_workbook,
    load_workbook_tables,
    solve_reviewed_tables,
    solve_workbook,
    validate_draft_tables,
)

st.set_page_config(page_title='Cross Hardy Pipe Tool', layout='wide')

APP_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = APP_DIR / 'cross_hardy_pipe_tool_import_template.xlsx'

st.title('Cross Hardy Pipe Tool')
st.write('Upload a piping plan to build a draft workbook, review it, and then run the model.')

mode = st.radio(
    'Choose workflow',
    ['Build workbook from piping plan', 'Review existing workbook'],
    horizontal=True,
)


def init_state(key, default):
    if key not in st.session_state:
        st.session_state[key] = default


for key, default in [
    ('pdf_draft_nodes', None),
    ('pdf_draft_segments', None),
    ('pdf_draft_loads', None),
    ('pdf_draft_sources', None),
    ('pdf_draft_warnings', []),
    ('pdf_draft_text_preview', ''),
    ('pdf_validation_result', None),
    ('pdf_solver_output', None),
    ('wb_nodes', None),
    ('wb_segments', None),
    ('wb_loads', None),
    ('wb_sources', None),
    ('wb_validation_result', None),
    ('wb_solver_output', None),
]:
    init_state(key, default)


def render_solver_results(summary, nodes_df, pipes_df, map_bytes):
    nodes_df = nodes_df.rename(columns={
        'node_id': 'node_id',
        'demand': 'demand_scfm',
        'is_source': 'is_source',
        'source_pressure': 'source_pressure_psig',
        'pressure': 'pressure_psig',
        'drop_from_reference': 'drop_from_reference_psi',
        'x': 'x_relative',
        'y': 'y_relative',
    })

    pipes_df = pipes_df.rename(columns={
        'pipe_id': 'pipe_id',
        'from_node': 'from_node',
        'to_node': 'to_node',
        'length': 'length_ft',
        'diameter': 'diameter_in',
        'K': 'K_psi_per_scfm2',
        'loss_exponent': 'loss_exponent',
        'status': 'status',
        'flow': 'flow_scfm',
        'dP_signed': 'dP_signed_psi',
        'dP_abs': 'dP_abs_psi',
        'relative_velocity_metric': 'relative_velocity_metric_scfm_per_in2',
        'velocity_ft_s': 'velocity_ft_s',
        'direction': 'direction',
        'loops': 'loops',
    })

    st.subheader('Summary')
    c1, c2, c3 = st.columns(3)
    c4, c5, c6 = st.columns(3)

    c1.metric('Worst Node Pressure (psig)', f"{summary['worst_node_pressure_psig']:.3f}")
    c2.metric('Worst Node Drop (psi)', f"{summary['worst_node_drop_psi']:.3f}")
    c3.metric('Max Segment Velocity (ft/s)', f"{summary['max_velocity_ft_s']:.3f}")
    c4.metric('Max Relative Velocity Metric (scfm/in²)', f"{summary['max_relative_velocity_metric']:.3f}")
    c5.metric('Loop Count', f"{summary.get('loop_count', 0)}")
    c6.metric('Iterations', f"{summary.get('iterations', 0)}")

    st.caption(
        f"Reference source: {summary['reference_source']} @ "
        f"{summary['reference_pressure_psig']:.3f} psig | "
        f"Worst node: {summary['worst_node_pressure_id']} | "
        f"Worst drop node: {summary['worst_node_drop_id']} | "
        f"Max velocity pipe: {summary['max_velocity_ft_s_pipe_id']} | "
        f"Max relative velocity pipe: {summary['max_relative_velocity_metric_pipe_id']}"
    )

    st.write('### Nodes Results')
    st.dataframe(nodes_df, use_container_width=True)

    st.write('### Pipes Results')
    st.dataframe(pipes_df, use_container_width=True)

    if map_bytes:
        st.write('### Network Map')
        st.image(map_bytes, caption='Pipe Network Map', use_container_width=True)

    nodes_csv = nodes_df.to_csv(index=False).encode('utf-8')
    pipes_csv = pipes_df.to_csv(index=False).encode('utf-8')

    st.download_button('Download nodes results CSV', data=nodes_csv, file_name='nodes_results.csv', mime='text/csv')
    st.download_button('Download pipes results CSV', data=pipes_csv, file_name='pipes_results.csv', mime='text/csv')


def render_review_workflow(prefix: str, heading: str, download_name: str):
    nodes_key = f'{prefix}_nodes'
    segments_key = f'{prefix}_segments'
    loads_key = f'{prefix}_loads'
    sources_key = f'{prefix}_sources'
    validation_key = f'{prefix}_validation_result'
    solver_output_key = f'{prefix}_solver_output'

    if st.session_state[nodes_key] is None:
        return

    st.write(f'## {heading}')

    if prefix == 'pdf':
        for warning in st.session_state['pdf_draft_warnings']:
            st.warning(warning)

        with st.expander('Extracted text preview', expanded=False):
            st.code(st.session_state['pdf_draft_text_preview'][:4000])

    with st.form(f'{prefix}_review_form'):
        tab1, tab2, tab3, tab4 = st.tabs(['Nodes', 'Segments', 'Loads', 'Sources'])

        with tab1:
            edited_nodes = st.data_editor(
                st.session_state[nodes_key],
                num_rows='dynamic',
                use_container_width=True,
                key=f'{prefix}_nodes_editor',
            )

        with tab2:
            edited_segments = st.data_editor(
                st.session_state[segments_key],
                num_rows='dynamic',
                use_container_width=True,
                key=f'{prefix}_segments_editor',
            )

        with tab3:
            edited_loads = st.data_editor(
                st.session_state[loads_key],
                num_rows='dynamic',
                use_container_width=True,
                key=f'{prefix}_loads_editor',
            )

        with tab4:
            edited_sources = st.data_editor(
                st.session_state[sources_key],
                num_rows='dynamic',
                use_container_width=True,
                key=f'{prefix}_sources_editor',
            )

        save_edits = st.form_submit_button('Save table edits')

    if save_edits:
        st.session_state[nodes_key] = edited_nodes
        st.session_state[segments_key] = edited_segments
        st.session_state[loads_key] = edited_loads
        st.session_state[sources_key] = edited_sources
        st.success('Table edits saved.')

    c1, c2 = st.columns(2)

    with c1:
        if st.button('Validate reviewed data', key=f'{prefix}_validate_btn'):
            result = validate_draft_tables(
                nodes_df=st.session_state[nodes_key],
                segments_df=st.session_state[segments_key],
                loads_df=st.session_state[loads_key],
                sources_df=st.session_state[sources_key],
            )
            st.session_state[validation_key] = result
            st.session_state[solver_output_key] = None

    with c2:
        try:
            workbook_bytes = export_reviewed_draft_workbook(
                template_path=TEMPLATE_PATH,
                nodes_df=st.session_state[nodes_key],
                segments_df=st.session_state[segments_key],
                loads_df=st.session_state[loads_key],
                sources_df=st.session_state[sources_key],
            )
            st.download_button(
                'Download reviewed workbook',
                data=workbook_bytes,
                file_name=download_name,
                mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                key=f'{prefix}_download_btn',
            )
        except Exception as e:
            st.info(f'Workbook export not ready yet: {e}')

    result = st.session_state[validation_key]
    if result is not None:
        st.write('## Validation')

        if result['stats']:
            st.json(result['stats'])

        for warning in result['warnings']:
            st.warning(warning)

        if result['errors']:
            for error in result['errors']:
                st.error(error)
        else:
            st.success('Validation passed. You can run the solver.')

            if st.button('Run solver on reviewed workbook', key=f'{prefix}_run_solver_btn'):
                try:
                    output, workbook_bytes = solve_reviewed_tables(
                        template_path=TEMPLATE_PATH,
                        nodes_df=st.session_state[nodes_key],
                        segments_df=st.session_state[segments_key],
                        loads_df=st.session_state[loads_key],
                        sources_df=st.session_state[sources_key],
                    )
                    st.session_state[solver_output_key] = output
                    st.success('Solver run completed.')
                except Exception as e:
                    st.error(f'Solver run failed: {e}')

    if st.session_state[solver_output_key] is not None:
        summary, nodes_df, pipes_df, map_bytes = st.session_state[solver_output_key]
        render_solver_results(summary, nodes_df, pipes_df, map_bytes)


if mode == 'Review existing workbook':
    uploaded_file = st.file_uploader('Upload Excel workbook', type=['xlsx'])

    if uploaded_file is not None:
        if st.button('Load workbook for review'):
            with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp:
                tmp.write(uploaded_file.read())
                temp_path = tmp.name

            try:
                tables = load_workbook_tables(temp_path)
                st.session_state['wb_nodes'] = tables['nodes']
                st.session_state['wb_segments'] = tables['segments']
                st.session_state['wb_loads'] = tables['loads']
                st.session_state['wb_sources'] = tables['sources']
                st.session_state['wb_validation_result'] = None
                st.session_state['wb_solver_output'] = None
                st.success('Workbook loaded. Review or edit the tables before solving.')
            except Exception as e:
                st.error(f'Workbook load failed: {e}')
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)

    render_review_workflow('wb', 'Workbook Review', 'cross_hardy_pipe_tool_reviewed_workbook.xlsx')

else:
    uploaded_pdf = st.file_uploader('Upload piping plan PDF', type=['pdf'])

    default_source_pressure = st.number_input(
        'Default source pressure for draft source row (psig)',
        min_value=0.0,
        value=30.0,
        step=1.0,
    )

    if uploaded_pdf is not None:
        if st.button('Build draft workbook'):
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
                pdf_bytes = uploaded_pdf.read()
                tmp.write(pdf_bytes)
                temp_pdf_path = tmp.name

            parsed_text = None
            uploaded_name = uploaded_pdf.name
            candidate = APP_DIR / 'sample_data' / f'{uploaded_name}.parsed.txt'
            if candidate.exists():
                parsed_text = candidate.read_text(encoding='utf-8', errors='ignore')
                with open(f'{temp_pdf_path}.parsed.txt', 'w', encoding='utf-8') as f:
                    f.write(parsed_text)

            try:
                draft = build_draft_tables(
                    pdf_path=temp_pdf_path,
                    template_path=TEMPLATE_PATH,
                    default_source_pressure=default_source_pressure,
                )
                st.session_state['pdf_draft_nodes'] = draft['nodes']
                st.session_state['pdf_draft_segments'] = draft['segments']
                st.session_state['pdf_draft_loads'] = draft['loads']
                st.session_state['pdf_draft_sources'] = draft['sources']
                st.session_state['pdf_draft_warnings'] = draft['warnings']
                st.session_state['pdf_draft_text_preview'] = draft['raw_text_preview']
                st.session_state['pdf_validation_result'] = None
                st.session_state['pdf_solver_output'] = None
                st.success('Draft tables created. Review them carefully before solving.')
            except Exception as e:
                st.error(f'Draft generation failed: {e}')
            finally:
                if os.path.exists(temp_pdf_path):
                    os.remove(temp_pdf_path)
                if os.path.exists(f'{temp_pdf_path}.parsed.txt'):
                    os.remove(f'{temp_pdf_path}.parsed.txt')

    render_review_workflow('pdf', 'Draft Review', 'cross_hardy_pipe_tool_reviewed_draft.xlsx')
