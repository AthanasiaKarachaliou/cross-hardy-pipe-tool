from __future__ import annotations

import os
import tempfile
from pathlib import Path

import streamlit as st

from cross_hardy_pipe_tool_plan_import import build_draft_tables
from cross_hardy_pipe_tool_solver import (
    export_reviewed_draft_workbook,
    load_review_tables_from_workbook,
    solve_reviewed_tables,
    validate_draft_tables,
)

st.set_page_config(page_title='Cross Hardy Pipe Tool', layout='wide')

APP_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = APP_DIR / 'cross_hardy_pipe_tool_import_template.xlsx'

st.title('Cross Hardy Pipe Tool')
st.write('Upload a piping plan to build a draft workbook, or upload an existing workbook to review, edit, and run it again.')

mode = st.radio(
    'Choose workflow',
    ['Build workbook from piping plan', 'Review existing workbook'],
    horizontal=True,
)

for key, default in {
    'draft_nodes': None,
    'draft_segments': None,
    'draft_loads': None,
    'draft_sources': None,
    'draft_warnings': [],
    'draft_text_preview': '',
    'draft_validation_result': None,
    'draft_solver_output': None,
    'draft_loaded_name': None,
    'workbook_nodes': None,
    'workbook_segments': None,
    'workbook_loads': None,
    'workbook_sources': None,
    'workbook_validation_result': None,
    'workbook_solver_output': None,
    'workbook_loaded_name': None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default


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
        'velocity_relative': 'velocity_relative',
        'direction': 'direction',
        'loops': 'loops',
    })

    st.subheader('Summary')
    c1, c2, c3 = st.columns(3)
    c1.metric('Worst Node Pressure (psig)', f"{summary['worst_node_pressure_psig']:.3f}")
    c2.metric('Worst Node Drop (psi)', f"{summary['worst_node_drop_psi']:.3f}")
    c3.metric('Max Relative Velocity', f"{summary['max_velocity_relative']:.3f}")

    extra_cols = []
    if 'loop_count' in summary:
        extra_cols.append(('Loop Count', summary['loop_count']))
    if 'iterations' in summary:
        extra_cols.append(('Iterations', summary['iterations']))
    if 'converged' in summary:
        extra_cols.append(('Converged', summary['converged']))
    if extra_cols:
        cols = st.columns(len(extra_cols))
        for col, (label, value) in zip(cols, extra_cols):
            col.metric(label, str(value))

    st.caption(
        f"Reference source: {summary['reference_source']} @ {summary['reference_pressure_psig']:.3f} psig | "
        f"Worst node: {summary['worst_node_pressure_id']} | "
        f"Worst drop node: {summary['worst_node_drop_id']} | "
        f"Max relative velocity pipe: {summary['max_velocity_relative_pipe_id']}"
    )

    st.write('### Nodes Results')
    st.dataframe(nodes_df, use_container_width=True)

    st.write('### Pipes Results')
    st.dataframe(pipes_df, use_container_width=True)

    if map_bytes:
        st.write('### Network Map')
        map_col, legend_col = st.columns([4, 1.4])
        with map_col:
            st.image(map_bytes, caption='Pipe Network Map', use_container_width=True)
        with legend_col:
            st.markdown('**Map Legend**')
            st.markdown(
                """
- **Pipe color** = relative velocity  
  - darker purple/blue = lower relative velocity  
  - orange/yellow = higher relative velocity  

- **Pipe thickness** = absolute flow magnitude  
  - thinner lines = lower flow  
  - thicker lines = higher flow  

- **Arrow direction** = calculated flow direction  

- **Node circles** = modeled network connection points
                """
            )

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

    if prefix == 'draft':
        for warning in st.session_state['draft_warnings']:
            st.warning(warning)
        with st.expander('Extracted text preview', expanded=False):
            st.code(st.session_state['draft_text_preview'][:4000])
    else:
        if st.session_state['workbook_loaded_name']:
            st.info(f"Loaded workbook: {st.session_state['workbook_loaded_name']}")

    st.info('Edit the tables below, then click Save table edits before validating or running the solver.')

    with st.form(f'{prefix}_review_form', clear_on_submit=False):
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
        st.session_state[validation_key] = None
        st.session_state[solver_output_key] = None
        st.success('Table edits saved.')

    c1, c2 = st.columns(2)
    with c1:
        if st.button('Validate reviewed workbook' if prefix == 'workbook' else 'Validate reviewed draft', key=f'{prefix}_validate_button'):
            st.session_state[validation_key] = validate_draft_tables(
                nodes_df=st.session_state[nodes_key],
                segments_df=st.session_state[segments_key],
                loads_df=st.session_state[loads_key],
                sources_df=st.session_state[sources_key],
            )

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
                key=f'{prefix}_download_button',
            )
        except Exception as e:
            st.info(f'Workbook export not ready yet: {e}')

    if st.session_state[validation_key] is not None:
        result = st.session_state[validation_key]
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
            if st.button('Run solver on reviewed workbook', key=f'{prefix}_run_solver_button'):
                try:
                    output, _ = solve_reviewed_tables(
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
        current_name = uploaded_file.name
        if st.session_state['workbook_loaded_name'] != current_name:
            with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp:
                tmp.write(uploaded_file.read())
                temp_path = tmp.name

            try:
                tables = load_review_tables_from_workbook(temp_path)
                st.session_state['workbook_nodes'] = tables['nodes']
                st.session_state['workbook_segments'] = tables['segments']
                st.session_state['workbook_loads'] = tables['loads']
                st.session_state['workbook_sources'] = tables['sources']
                st.session_state['workbook_validation_result'] = None
                st.session_state['workbook_solver_output'] = None
                st.session_state['workbook_loaded_name'] = current_name
                st.success('Workbook loaded into editable tables.')
            except Exception as e:
                st.error(f'Workbook load failed: {e}')
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)

    render_review_workflow(prefix='workbook', heading='Workbook Review', download_name='cross_hardy_pipe_tool_reviewed_workbook.xlsx')

else:
    uploaded_pdf = st.file_uploader('Upload piping plan PDF', type=['pdf'])
    default_source_pressure = st.number_input('Default source pressure for draft source row (psig)', min_value=0.0, value=30.0, step=1.0)
    use_parsed_text = st.checkbox('Use matching .parsed.txt file if available', value=True)

    if uploaded_pdf is not None and st.button('Build draft workbook'):
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
            pdf_bytes = uploaded_pdf.read()
            tmp.write(pdf_bytes)
            temp_pdf_path = tmp.name

        if use_parsed_text:
            candidate = APP_DIR / 'sample_data' / f'{uploaded_pdf.name}.parsed.txt'
            if candidate.exists():
                with open(f'{temp_pdf_path}.parsed.txt', 'w', encoding='utf-8') as f:
                    f.write(candidate.read_text(encoding='utf-8', errors='ignore'))

        try:
            draft = build_draft_tables(pdf_path=temp_pdf_path, template_path=TEMPLATE_PATH, default_source_pressure=default_source_pressure)
            st.session_state['draft_nodes'] = draft['nodes']
            st.session_state['draft_segments'] = draft['segments']
            st.session_state['draft_loads'] = draft['loads']
            st.session_state['draft_sources'] = draft['sources']
            st.session_state['draft_warnings'] = draft['warnings']
            st.session_state['draft_text_preview'] = draft['raw_text_preview']
            st.session_state['draft_validation_result'] = None
            st.session_state['draft_solver_output'] = None
            st.session_state['draft_loaded_name'] = uploaded_pdf.name
            st.success('Draft tables created. Review them carefully before solving.')
        except Exception as e:
            st.error(f'Draft generation failed: {e}')
        finally:
            if os.path.exists(temp_pdf_path):
                os.remove(temp_pdf_path)
            if os.path.exists(f'{temp_pdf_path}.parsed.txt'):
                os.remove(f'{temp_pdf_path}.parsed.txt')

    render_review_workflow(prefix='draft', heading='Draft Review', download_name='cross_hardy_pipe_tool_reviewed_draft.xlsx')
