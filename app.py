#------------------------------------------------------------------------------
# (c) 2020-2024 ANSYS, Inc. All rights reserved.
#------------------------------------------------------------------------------
import json
import os
import shutil
import sys
import traceback
import base64
import io

import dash
from datetime import datetime
import platform
import dash_core_components as dcc
import dash_bootstrap_components as dbc
import dash_html_components as html
from dash.dependencies import Input, Output, State
import pandas as pd
import csv
import glob
import time

from flask import send_from_directory

from twin_runtime.twin_runtime_core import TwinRuntime
from twin_runtime.twin_runtime_core import LogLevel

CUR_DIR = os.path.abspath(os.path.dirname(os.path.realpath(__file__)))

twin_model = os.path.join(CUR_DIR, "static", "model.twin")
csv_input = os.path.join(CUR_DIR, "static", "input.csv")
model_cfg = os.path.join(CUR_DIR, "static", "model_setup.json")

# 전역 변수로 실시간 시뮬레이션 상태 저장
simulation_state = {
    'current_row': 0,
    'results': [],
    'inputs': [],
    'times': [],
    'is_running': False,
    'total_rows': 0,
    'csv_path': None,
    'input_columns': [],
    'is_processing': False,
    'cached_df': None,
    'csv_mtime': 0,
    # ✅ ROM 관련 추가
    'model_names': [],
    'view_names': [],
    'output_directories': [],
    'timesteps': [],
    'rom_initialized': False
}


def model_setup_from_cfg(path):
    if not os.path.isfile(path):
        print('Skip twin properties override setup due to file not found:', path)
        return {}, {}
    with open(path) as file:
        cfg = json.load(file)

    cfg_input_map = cfg['model'].get('inputs')
    cfg_param_map = cfg['model'].get('parameters', {})

    return cfg_input_map, cfg_param_map


def init_runtime():
    pd.set_option("display.precision", 12)
    pd.set_option("display.max_columns", 20)
    pd.set_option("display.expand_frame_repr", False)

    if platform.system() == "Windows":
        lib_file = "TwinRuntimeSDK.dll"
    else:
        lib_file = "TwinRuntimeSDK.so"

    runtime_log = os.path.join(CUR_DIR, "model_{}.log".format(platform.system()))
    twin_model_input_df = load_data(csv_input)
    twin_runtime_lib = os.path.join(os.path.dirname(CUR_DIR), "twin_runtime", lib_file)
    twin_runtime = TwinRuntime(
        twin_model, runtime_log, log_level=LogLevel.TWIN_LOG_ERROR
    )

    return (
        runtime_log,
        twin_model_input_df,
        twin_runtime_lib,
        twin_runtime,
    )


def fetch_runtime_data():
    model_name = twin_runtime.twin_get_model_name()
    parameter_names = twin_runtime.twin_get_param_names()
    in_names = twin_runtime.twin_get_input_names()
    out_names = twin_runtime.twin_get_output_names()

    return model_name, parameter_names, in_names, out_names


def init_parameters_ui(twin_runtime, param_names):
    names = []
    rows = []
    states = []

    _, param_map = model_setup_from_cfg(model_cfg)

    for p in param_names:
        if p not in param_map:
            param_map[p] = twin_runtime.twin_get_var_start(p)

    if param_names is None:
        param_names = []
    for i, name in enumerate(param_names):
        id_ = f"variable-{i}"
        row = dbc.FormGroup(
            [
                dbc.Label(name, html_for=id_, width=8),
                dbc.Col(
                    dbc.InputGroup(
                        [
                            dbc.Input(
                                id=id_,
                                value=str(param_map[name]),
                                style={"text-align": "right"},
                            )
                        ],
                        size="sm",
                    ),
                    width=4,
                ),
            ],
            row=True,
            className="mb-2",
        )

        names.append(name)
        rows.append(row)
        states.append(State(id_, "value"))

    return states, rows, names


def load_data(twin_builder_inputs):
    """CSV 파일을 로드하여 DataFrame으로 반환"""
    def clean_column_names(column_names):
        for name_index in range(len(column_names)):
            clean_header = (
                column_names[name_index]
                .replace('"', "")
                .replace(" ", "")
                .replace("]", "")
                .replace("[", "")
            )
            name_components = clean_header.split(".", 1)
            column_names[name_index] = name_components[-1]

        return column_names

    input_header_df = pd.read_csv(
        twin_builder_inputs,
        header=None,
        nrows=1,
        sep=r",\s+",
        engine="python",
        quoting=csv.QUOTE_ALL,
    )

    twin_builder_inputs_df = pd.read_csv(twin_builder_inputs, header=None, skiprows=1)

    inputs_header_values = input_header_df.iloc[0][0].split(",")
    clean_column_names(inputs_header_values)
    twin_builder_inputs_df.columns = inputs_header_values

    print(f"[DEBUG] Loaded CSV with {len(twin_builder_inputs_df)} rows")
    if len(twin_builder_inputs_df) > 0:
        print(f"[DEBUG] Time range: {twin_builder_inputs_df['Time'].min()} to {twin_builder_inputs_df['Time'].max()}")

    return twin_builder_inputs_df


def read_new_csv_lines(csv_path, last_read_line):
    """
    ✅ 캐싱 최적화: CSV에서 새로운 줄만 읽어오기 (파일 수정 시간 기반)
    """
    global simulation_state

    try:
        current_mtime = os.path.getmtime(csv_path)
        cached_mtime = simulation_state.get('csv_mtime', 0)
        cached_df = simulation_state.get('cached_df')

        if cached_df is not None and current_mtime == cached_mtime:
            total_rows = len(cached_df)

            if total_rows > last_read_line:
                new_data_count = total_rows - last_read_line
                print(f"[INFO] 📊 Using cached data: {new_data_count} new rows to process (Total: {total_rows})")
                return cached_df, total_rows

            return None, last_read_line

        print(f"[INFO] 🔄 CSV file modified or no cache, reloading...")
        twin_model_input_df = load_data(csv_path)
        total_rows = len(twin_model_input_df)

        simulation_state['cached_df'] = twin_model_input_df
        simulation_state['csv_mtime'] = current_mtime

        if total_rows > last_read_line:
            new_data_count = total_rows - last_read_line
            print(f"[INFO] 🆕 New data detected: {new_data_count} new rows (Total: {total_rows})")
            return twin_model_input_df, total_rows

        return None, last_read_line

    except Exception as e:
        print(f"[ERROR] Failed to read CSV: {e}")
        traceback.print_exc()
        return None, last_read_line


def run_simulation_step_by_step(csv_path, twin_runtime, current_step):
    """
    ✅ 한 줄씩 시뮬레이션 실행 + ROM 이미지 실시간 생성
    """
    global simulation_state

    twin_model_input_df, total_rows = read_new_csv_lines(csv_path, current_step)

    if twin_model_input_df is None:
        return None, None, None, None, current_step, False

    if current_step >= total_rows:
        return None, None, None, None, total_rows, False

    current_input = twin_model_input_df.iloc[current_step]
    time_value = current_input['Time']
    input_values = list(current_input[1:])

    # 시뮬레이션 실행
    twin_runtime.twin_set_inputs(input_values)
    twin_runtime.twin_simulate(time_value)

    output_values = twin_runtime.twin_get_outputs()

    # ✅ ROM 이미지 실시간 생성 (ROM이 설정된 경우)
    model_names = simulation_state.get('model_names', [])
    view_names = simulation_state.get('view_names', [])
    output_directories = simulation_state.get('output_directories', [])

    if model_names and simulation_state.get('rom_initialized', False):
        try:
            # ROM 이미지 생성 트리거 (twin_simulate 후 자동 생성됨)
            # 생성된 이미지 timesteps 업데이트
            updated_timesteps = []
            for i, model_name in enumerate(model_names):
                model_view_names = view_names[i]
                ts = get_time_values(model_name, model_view_names)
                updated_timesteps.append(ts)

            simulation_state['timesteps'] = updated_timesteps

            # 새로 생성된 이미지 수 출력
            total_images = sum(len(ts[0]) if ts and len(ts) > 0 else 0 for ts in updated_timesteps)
            print(f"[INFO] 🎨 Step {current_step + 1}: Time={time_value:.3f}, ROM images: {total_images}")

        except Exception as e:
            print(f"[WARNING] ROM image update failed at step {current_step}: {e}")

    print(f"[INFO] ⚙️ Step {current_step + 1}/{total_rows}: Time={time_value:.3f}, Outputs={[f'{v:.6f}' for v in output_values]}")

    return time_value, output_values, input_values, twin_model_input_df.columns.tolist(), total_rows, True


def create_plotly_figure_realtime(times, results_data, inputs_data, input_columns, output_columns, window_size=20):
    """
    실시간으로 업데이트되는 그래프 생성 - 슬라이딩 윈도우 (최근 20초)
    """
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    time_unit = "s"

    num_outputs = len(output_columns)
    num_inputs = len(input_columns) - 1
    total_plots = num_outputs + num_inputs

    fig = make_subplots(rows=total_plots, cols=1, shared_xaxes=True)

    if len(times) > 0:
        current_time = times[-1]

        if current_time <= window_size:
            x_min = 0
            x_max = window_size
        else:
            x_min = current_time - window_size
            x_max = current_time

        filtered_indices = [i for i, t in enumerate(times) if x_min <= t <= x_max]

        if filtered_indices:
            filtered_times = [times[i] for i in filtered_indices]
            filtered_results = [results_data[i] for i in filtered_indices]
            filtered_inputs = [inputs_data[i] for i in filtered_indices]
        else:
            filtered_times = times
            filtered_results = results_data
            filtered_inputs = inputs_data
    else:
        filtered_times = times
        filtered_results = results_data
        filtered_inputs = inputs_data
        x_min = 0
        x_max = window_size

    for i, col_name in enumerate(output_columns):
        y_data = [row[i] if row else None for row in filtered_results]
        fig.add_trace(
            go.Scatter(
                x=filtered_times,
                y=y_data,
                name=col_name,
                mode='lines+markers',
                line=dict(color="rgba(18, 102, 61, 1)", width=2),
                marker=dict(size=4)
            ),
            row=i + 1,
            col=1,
        )
        fig['layout'][f'yaxis{i + 1}'].update(title=col_name)

    for j, col_idx in enumerate(range(1, len(input_columns))):
        col_name = input_columns[col_idx]
        y_data = [row[col_idx-1] if row else None for row in filtered_inputs]

        fig.add_trace(
            go.Scatter(
                x=filtered_times,
                y=y_data,
                name=col_name,
                mode='lines+markers',
                line=dict(color="rgba(152, 122, 17, 1)", width=2),
                marker=dict(size=4)
            ),
            row=num_outputs + j + 1,
            col=1,
        )
        fig['layout'][f'yaxis{num_outputs + j + 1}'].update(title=col_name)

    fig['layout']['height'] = 160 * total_plots + 30 * max(0, 5 - total_plots)
    fig['layout']['margin']['t'] = 30
    fig['layout']['margin']['b'] = 0
    fig['layout']['margin']['r'] = 30
    fig['layout']['plot_bgcolor'] = "rgba(0,0,0,0)"
    fig['layout'][f'xaxis{total_plots}'].update(title=f"time [{time_unit}]")

    fig.update_xaxes(range=[x_min, x_max])

    fig.update_xaxes(
        showgrid=True,
        gridwidth=1,
        ticklen=0,
        gridcolor="LightGrey",
        linecolor="black",
        showline=True,
        zeroline=True,
        zerolinewidth=1,
        zerolinecolor="LightGrey",
    )
    fig.update_yaxes(
        showgrid=True,
        gridwidth=1,
        ticklen=0,
        gridcolor="LightGrey",
        linecolor="black",
        showline=True,
        zerolinewidth=1,
        zerolinecolor="LightGrey",
    )

    fig.update_layout(showlegend=False)

    return fig


def initialize_simulation(param_values, input_df=None):
    """
    ✅ 시뮬레이션 초기화 - ROM 설정만 수행 (배치 생성 제거)
    """
    global simulation_state

    if input_df is None:
        temp_csv_path = os.path.join(CUR_DIR, "static", "temp_input.csv")
        if os.path.exists(temp_csv_path):
            csv_path = temp_csv_path
        else:
            csv_path = csv_input
    else:
        csv_path = os.path.join(CUR_DIR, "static", "temp_input.csv")
        input_df.to_csv(csv_path, index=False)

    print(f"[DEBUG] CSV path determined: {csv_path}")

    twin_model_input_df = load_data(csv_path)

    simulation_state['cached_df'] = twin_model_input_df
    simulation_state['csv_mtime'] = os.path.getmtime(csv_path)

    # ✅ Twin Runtime 생성 및 ROM 설정
    print(f"[INFO] 🎬 Initializing real-time simulation with ROM...")
    twin_runtime = TwinRuntime(
        twin_model, runtime_log, log_level=LogLevel.TWIN_LOG_ERROR
    )
    twin_runtime.twin_instantiate()

    start_values = list(twin_model_input_df.iloc[0, 1:])
    twin_runtime.twin_set_inputs(start_values)

    for val, p_name in zip(param_values, parameters):
        twin_runtime.twin_set_param_by_name(p_name, float(val))

    # ✅ ROM 설정 (이미지 생성은 각 스텝에서 실시간으로)
    model_names, view_names, output_directories = setup_rom_viz(twin_runtime)

    twin_runtime.twin_initialize()

    # ✅ 초기 timesteps (아직 이미지 없음)
    timesteps = []
    for model_name in model_names:
        model_view_names = view_names[model_names.index(model_name)]
        timesteps.append(get_time_values(model_name, model_view_names))

    simulation_state.update({
        'current_row': 0,
        'results': [],
        'inputs': [],
        'times': [],
        'is_running': True,
        'total_rows': len(twin_model_input_df),
        'twin_runtime': twin_runtime,
        'csv_path': csv_path,
        'input_columns': twin_model_input_df.columns.tolist(),
        'is_processing': False,
        'model_names': model_names,
        'view_names': view_names,
        'output_directories': output_directories,
        'timesteps': timesteps,
        'rom_initialized': True
    })

    print(f"[INFO] ✅ Initialization complete!")
    print(f"[INFO] - Total steps: {simulation_state['total_rows']}")
    print(f"[INFO] - ROM models: {len(model_names)}")
    print(f"[INFO] - ROM mode: Real-time (step-by-step)")

    return twin_runtime, csv_path


def needs_extra_subdirectory(twin_runtime, rom_name):
    resource_folder = twin_runtime.twin_get_rom_resource_directory(rom_name)
    rom_xml = os.path.join(os.path.dirname(resource_folder), 'modelDescription.xml')
    try:
        with open(rom_xml, 'r') as f:
            _ = f.read()
        return True
    except Exception as e:
        return False


def setup_rom_viz(twin_runtime):
    model_names = []
    view_names = []
    output_directories = []

    visualization_info = twin_runtime.twin_get_visualization_resources()

    print(f"[DEBUG] Visualization info: {visualization_info}")

    if visualization_info:
        for model_name, info in visualization_info.items():
            directory_path = os.path.join(os.path.dirname(twin_model), "runtime_images")

            if not os.path.isdir(directory_path):
                os.makedirs(directory_path)
                print(f"[INFO] Created directory: {directory_path}")

            if type(info["views"]) == dict:
                views = list(info["views"].keys())

                model_names.append((format(model_name)))
                view_names.append(list(info["views"].values()))
                output_directories.append(directory_path)

                if needs_extra_subdirectory(twin_runtime, model_name):
                    rom_dir = os.path.join(directory_path, model_name)
                    os.makedirs(rom_dir, exist_ok=True)
                    twin_runtime.twin_set_rom_image_directory(model_name, rom_dir)
                    print(f"[INFO] ROM image directory set (with subdir): {rom_dir}")
                else:
                    twin_runtime.twin_set_rom_image_directory(model_name, directory_path)
                    print(f"[INFO] ROM image directory set: {directory_path}")

                twin_runtime.twin_enable_rom_model_images(model_name, views)
                print(f"[INFO] ROM images enabled for {model_name} with views: {views}")

                twin_runtime.twin_enable_3d_rom_model_data(model_name)
                print(f"[INFO] 3D ROM data enabled for {model_name}")

    print(f"[INFO] ROM setup completed: {len(model_names)} models found")
    return model_names, view_names, output_directories


def get_time_values(model_name, model_view_names):
    """ROM 이미지 파일에서 timesteps 추출"""
    file_path = f"static/runtime_images/{model_name}/"
    view_timestep = []

    abs_file_path = os.path.join(CUR_DIR, file_path)
    filename_list = glob.glob(abs_file_path + "*.png")

    print(f"[DEBUG] Scanning for images in: {abs_file_path}")
    print(f"[DEBUG] Found {len(filename_list)} PNG files")

    for viewname in model_view_names:
        tmp = []
        for filename in filename_list:
            basename = os.path.basename(filename)
            if basename.find(viewname + "_") != -1:
                start = basename.find(viewname + "_") + len(viewname + "_")
                end = basename.find(".png")
                time_str = basename[start:end]
                tmp.append(time_str)

        tmp.sort(key=lambda i: float(i))
        view_timestep.append(tmp)
        print(f"[DEBUG] View '{viewname}' has {len(tmp)} timesteps")

    return view_timestep


app = dash.Dash('app', external_stylesheets=[dbc.themes.FLATLY])

(
    runtime_log,
    twin_model_input_df,
    twin_runtime_lib,
    twin_runtime,
) = init_runtime()
model_name, parameters, in_list, out_list = fetch_runtime_data()
parameter_states, rows, names = init_parameters_ui(twin_runtime, parameters)

app.layout = dbc.Container(
    [
        html.Div(
            [
                html.H1(
                    "Ansys Twin Builder WebApp (Real-time Mode)",
                    className="text-center text-primary mb-4 mt-5"
                ),
                html.Div(id="output_container", children=[]),
                dbc.Tabs(
                    [
                        dbc.Tab(label="Model Info", tab_id="model_info_tab"),
                        dbc.Tab(label="Simulation Result", tab_id="simulation_tab"),
                        dbc.Tab(label="ROM Visualization", tab_id="image_output_tab"),
                    ],
                    className="pt-4 mb-4",
                    active_tab="simulation_tab",
                    id="tabs",
                ),
                dbc.Container(
                    [
                        html.H2("Model Input Info"),
                        html.Div(id="model_store", children=[], style={"display": "none"}),
                        html.Div(id="view_2d_array_store", children=[], style={"display": "none"}),
                        html.Div(id="timestep_2d_array_store", children=[], style={"display": "none"}),
                        html.Div(id="csv_data_store", children=[], style={"display": "none"}),

                        dbc.Row(
                            [
                                dbc.Col(
                                    [
                                        dbc.Row(
                                            [
                                                dbc.Col(html.Span("Number of Inputs"), width=4),
                                                dbc.Col(
                                                    html.Span(
                                                        "{0} ({1})".format(len(in_list), ', '.join(in_list))
                                                    ),
                                                    width=8,
                                                ),
                                            ],
                                            className="py-1",
                                        ),
                                        dbc.Row(
                                            [
                                                dbc.Col(
                                                    html.Span("Number of Outputs"), width=4
                                                ),
                                                dbc.Col(
                                                    html.Span(
                                                        "{0} ({1})".format(len(out_list), ', '.join(out_list))
                                                    ),
                                                    width=8,
                                                ),
                                            ],
                                            className="py-1",
                                        ),
                                        dbc.Row(
                                            [
                                                dbc.Col(
                                                    html.Span("Number of Parameters"), width=4
                                                ),
                                                dbc.Col(
                                                    html.Span(
                                                        twin_runtime.twin_get_number_params()
                                                    ),
                                                    width=8,
                                                ),
                                            ],
                                            className="py-1",
                                        ),
                                        dbc.Row(
                                            [
                                                dbc.Col(html.Span("Input File"), width=4),
                                                dbc.Col(html.Span(csv_input), width=8),
                                            ],
                                            className="py-1",
                                        ),
                                        dbc.Row(
                                            [
                                                dbc.Col(html.Span("Twin File"), width=4),
                                                dbc.Col(html.Span(twin_model), width=8),
                                            ],
                                            className="py-1",
                                        ),
                                    ],
                                    width=8,
                                    className="mb-4",
                                ),
                            ]
                        ),
                    ],
                    className="embed-responsive",
                    id="model_info_container",
                ),
                dbc.Container(
                    [
                        html.H2("Simulation Info"),
                        dbc.Row(
                            [
                                dbc.Col(
                                    html.H2(
                                        model_name,
                                        className="text-center mb-4 mt-4",
                                        style={"color": "#FFB71B"},
                                    )
                                )
                            ]
                        ),

                        dbc.Form(
                            [
                                dbc.Button(
                                    "Start",
                                    id="simulate-button",
                                    color="primary",
                                    className="mr-2",
                                ),
                                dbc.Button(
                                    "Pause",
                                    id="pause-button",
                                    color="warning",
                                    className="mr-2",
                                    disabled=True,
                                ),
                                dbc.Button(
                                    "Stop",
                                    id="stop-button",
                                    color="danger",
                                    className="mr-4",
                                ),
                                html.Span(id="progress-text", className="ml-4",
                                         style={"font-weight": "bold", "font-size": "16px"}),
                            ],
                            inline=True,
                        ),

                        html.Div(rows, style={"display": "none"}),

                        dcc.Interval(
                            id='realtime-interval',
                            interval=200,
                            n_intervals=0,
                            disabled=True
                        ),

                        dbc.Row(
                            [
                                dbc.Col(id="result-col", width=12),
                            ],
                            className="mt-4",
                        ),
                    ],
                    className="embed-responsive",
                    id="simulation_container",
                ),
                dbc.Container(
                    [
                        html.H2("ROM Visualization"),
                        dcc.Dropdown(id="image_model_dropdown_input"),

                        html.Div(
                            [
                                html.H3("3D view"),
                                html.Div(
                                    [
                                        html.A(
                                            "Open 3D viewer",
                                            href='/ViewerGL/ROMViewer.html',
                                            target='_blank',
                                            id='open-viewer-link'
                                        ),
                                    ],
                                    className='p-4'
                                ),

                                html.H3("Image views"),
                                html.Div(
                                    [
                                        dcc.Dropdown(
                                            id="image_view_dropdown_input",
                                        ),
                                        html.Div(
                                            dcc.Slider(
                                                id="timestep_slider",
                                                min=0,
                                                step=1,
                                                updatemode='drag',
                                                disabled=True
                                            ),
                                            style={"margin-top": 5}
                                        ),
                                        html.Div(id="image_out_container", style={"margin-top": 5}),
                                    ],
                                    className='p-4'
                                )
                            ],
                            id='rom-selected',
                            className='p-4'
                        )
                    ],
                    className="embed-responsive",
                    id="image_output_container",
                ),
            ],
            id="content-wrap",
        ),
        html.Footer(
            [
                html.A(
                    "Ansys Twin Builder",
                    href="https://www.ansys.com/products/digital-twin/ansys-twin-builder",
                    className="d-block text-muted small",
                ),
            ],
            className="my-4 pt-3 border-top",
            id='footer'
        ),
    ],
    id='page-container'
)
app.title = model_name

twin_runtime.twin_close()


@app.callback(
    [
        Output(component_id="model_info_container", component_property="style"),
        Output(component_id="simulation_container", component_property="style"),
        Output(component_id="image_output_container", component_property="style"),
    ],
    [Input("tabs", "active_tab")],
)
def switch_tab(active_tab):
    return (
        {"display": "block" if active_tab == "model_info_tab" else "none"},
        {"display": "block" if active_tab == "simulation_tab" else "none"},
        {
            "display": "block" if active_tab == "image_output_tab" else "none",
            "padding-bottom": "10em"
        },
    )


@app.callback(
    [
        Output('realtime-interval', 'disabled'),
        Output('output_container', 'children'),
        Output('pause-button', 'disabled'),
        Output('pause-button', 'children'),
        Output('simulate-button', 'disabled'),
        Output('model_store', 'children'),
        Output('view_2d_array_store', 'children'),
        Output('timestep_2d_array_store', 'children'),
    ],
    [
        Input('simulate-button', 'n_clicks'),
        Input('pause-button', 'n_clicks'),
        Input('stop-button', 'n_clicks')
    ],
    [State('csv_data_store', 'children'),
     State('pause-button', 'children')] + parameter_states,
    prevent_initial_call=True
)
def control_simulation(start_clicks, pause_clicks, stop_clicks, csv_data, pause_label, *param_values):
    global simulation_state

    ctx = dash.callback_context
    if not ctx.triggered:
        return True, "", True, "Pause", False, [], [], []

    button_id = ctx.triggered[0]['prop_id'].split('.')[0]
    print(f"[DEBUG] Button clicked: {button_id}")

    if button_id == 'simulate-button':
        try:
            print(f"[DEBUG] Starting simulation with {len(param_values)} parameters")
            input_df = None
            if csv_data:
                try:
                    input_df = pd.read_json(csv_data, orient='split')
                    print(f"[DEBUG] Loaded CSV from store")
                except:
                    pass

            twin_runtime, csv_path = initialize_simulation(param_values, input_df)

            # ✅ ROM 정보 가져오기 (초기에는 빈 timesteps)
            model_names = simulation_state.get('model_names', [])
            view_names = simulation_state.get('view_names', [])
            timesteps = simulation_state.get('timesteps', [])

            print(f"[DEBUG] Returning to UI:")
            print(f"  - ROM models: {len(model_names)}")
            print(f"  - ROM views: {len(view_names)}")
            print(f"  - Initial timesteps: {len(timesteps)}")

            return (
                False,
                dbc.Alert("✅ Simulation started! ROM images will generate in real-time.", color="success", dismissable=True),
                False,
                "Pause",
                True,
                model_names,
                view_names,
                timesteps
            )
        except Exception as e:
            print(f"[ERROR] Failed to start simulation:")
            traceback.print_exc()
            return True, dbc.Alert(f"❌ Failed to start: {e}", color="danger", dismissable=True), True, "Pause", False, [], [], []

    elif button_id == 'pause-button':
        if pause_label == "Pause":
            simulation_state['is_running'] = False
            return True, dbc.Alert("⏸️ Simulation paused", color="warning", dismissable=True), False, "Resume", True, dash.no_update, dash.no_update, dash.no_update
        else:
            simulation_state['is_running'] = True
            return False, dbc.Alert("▶️ Simulation resumed", color="info", dismissable=True), False, "Pause", True, dash.no_update, dash.no_update, dash.no_update

    elif button_id == 'stop-button':
        simulation_state['is_running'] = False
        if 'twin_runtime' in simulation_state:
            simulation_state['twin_runtime'].twin_close()
        return True, dbc.Alert("🛑 Simulation stopped", color="danger", dismissable=True), True, "Pause", False, dash.no_update, dash.no_update, dash.no_update

    return True, "", True, "Pause", False, [], [], []


@app.callback(
    [
        Output('result-col', 'children'),
        Output('progress-text', 'children'),
        Output('timestep_2d_array_store', 'children'),  # ✅ timesteps 동적 업데이트
    ],
    [Input('realtime-interval', 'n_intervals')],
    prevent_initial_call=True
)
def update_realtime_graph(n):
    global simulation_state

    if not simulation_state.get('is_running', False):
        return dash.no_update, dash.no_update, dash.no_update

    if simulation_state.get('is_processing', False):
        return dash.no_update, dash.no_update, dash.no_update

    simulation_state['is_processing'] = True

    try:
        current_row = simulation_state['current_row']
        total_rows = simulation_state['total_rows']

        twin_runtime = simulation_state['twin_runtime']
        csv_path = simulation_state['csv_path']

        result = run_simulation_step_by_step(
            csv_path, twin_runtime, current_row
        )

        time_val, output_vals, input_vals, input_columns, new_total_rows, has_data = result

        if new_total_rows > total_rows:
            simulation_state['total_rows'] = new_total_rows
            total_rows = new_total_rows

        if not has_data:
            simulation_state['is_processing'] = False
            progress = f"⏸️ Waiting for new data... ({current_row}/{total_rows} processed)"

            if current_row >= total_rows and total_rows > 0:
                simulation_state['is_running'] = False
                progress = f"✅ Completed! ({current_row}/{total_rows} processed)"

            return dash.no_update, progress, dash.no_update

        if time_val is not None:
            simulation_state['times'].append(time_val)
            simulation_state['results'].append(output_vals)
            simulation_state['inputs'].append(input_vals)
            simulation_state['current_row'] += 1
            simulation_state['input_columns'] = input_columns

            fig = create_plotly_figure_realtime(
                simulation_state['times'],
                simulation_state['results'],
                simulation_state['inputs'],
                input_columns,
                list(twin_runtime.twin_get_output_names())
            )

            progress = f"⚙️ Running: {current_row + 1}/{total_rows} steps ({(current_row + 1)/total_rows*100:.1f}%)"

            # ✅ timesteps 업데이트 반환
            updated_timesteps = simulation_state.get('timesteps', [])

            simulation_state['is_processing'] = False
            return dcc.Graph(figure=fig, config={'displayModeBar': True}), progress, updated_timesteps

    except Exception as e:
        print(f"[ERROR] Update failed:")
        traceback.print_exc()
        simulation_state['is_running'] = False
        simulation_state['is_processing'] = False
        return dbc.Alert(f"❌ Error: {str(e)}", color="danger"), "❌ Error occurred", dash.no_update

    simulation_state['is_processing'] = False
    return dash.no_update, dash.no_update, dash.no_update


@app.callback(
    Output(component_id="image_model_dropdown_input", component_property="options"),
    [Input(component_id="model_store", component_property="children")],
)
def set_model_options(model_names):
    return [{"label": name, "value": name} for name in model_names]


@app.callback(
    Output(component_id="rom-selected", component_property="style"),
    Output(component_id="open-viewer-link", component_property="href"),
    [Input(component_id="image_model_dropdown_input", component_property="value")]
)
def on_rom_selected(rom_name):
    if rom_name:
        return dict(display='block'), f'/ViewerGL/ROMViewer.html?resBasePath=/static/runtime_images/{rom_name}'
    else:
        return dict(display='none'), ''


@app.callback(
    Output(component_id="image_view_dropdown_input", component_property="options"),
    [
        Input(component_id="image_model_dropdown_input", component_property="value"),
        Input(component_id="view_2d_array_store", component_property="children"),
        Input(component_id="model_store", component_property="children"),
    ],
)
def set_view_options(model_dropdown_value, view_names, model_names):
    if not model_dropdown_value or not model_names or model_dropdown_value not in model_names:
        return []
    model_key = model_names.index(model_dropdown_value)

    if not view_names or model_key >= len(view_names):
        return []

    view = view_names[model_key]
    return [{"label": name, "value": name} for name in view]


@app.callback(
    Output(component_id="timestep_slider", component_property="max"),
    Output(component_id="timestep_slider", component_property="disabled"),
    [
        Input(component_id="image_model_dropdown_input", component_property="value"),
        Input(component_id="timestep_2d_array_store", component_property="children"),
        Input(component_id="image_view_dropdown_input", component_property="value"),
        Input(component_id="model_store", component_property="children"),
        Input(component_id="view_2d_array_store", component_property="children"),
    ],
)
def set_slider_options(model_name, timesteps, view_name, model_names, view_names):
    if None in (model_name, view_name):
        return 100, True

    if not timesteps or not model_names or model_name not in model_names:
        return 100, True

    model_key = model_names.index(model_name)

    if not view_names or model_key >= len(view_names) or view_name not in view_names[model_key]:
        return 100, True

    view_key = view_names[model_key].index(view_name)

    if model_key >= len(timesteps) or view_key >= len(timesteps[model_key]):
        return 100, True

    max_value = len(timesteps[model_key][view_key])
    if max_value == 0:
        return 100, True

    return max_value, False


@app.callback(
    Output(component_id="image_out_container", component_property="children"),
    [
        Input(component_id="image_model_dropdown_input", component_property="value"),
        Input(component_id="image_view_dropdown_input", component_property="value"),
        Input(component_id="timestep_slider", component_property="value"),
        Input(component_id="model_store", component_property="children"),
        Input(component_id="timestep_2d_array_store", component_property="children"),
        Input(component_id="view_2d_array_store", component_property="children"),
    ],
)
def display_result(model_name, view_name, slider_value, model_names, timesteps, view_names):
    if None in [model_name, view_name]:
        return None

    if slider_value is None:
        slider_value = 0

    if not model_names or model_name not in model_names:
        return html.Div("⏳ Waiting for simulation to start...")

    model_key = model_names.index(model_name)

    if not view_names or model_key >= len(view_names) or view_name not in view_names[model_key]:
        return html.Div("⏳ Loading view data...")

    view_key = view_names[model_key].index(view_name)

    if not timesteps or model_key >= len(timesteps) or view_key >= len(timesteps[model_key]):
        return html.Div("⏳ Generating ROM images in real-time... Please wait.")

    r_max = len(timesteps[model_key][view_key])

    if r_max == 0:
        return html.Div("⏳ No ROM images yet. Simulation in progress...")

    if slider_value >= r_max:
        slider_value = r_max - 1

    time = timesteps[model_key][view_key][slider_value]
    image_path = f"static/runtime_images/{model_name}/{view_name}_{time}.png"

    abs_image_path = os.path.join(CUR_DIR, image_path)
    if not os.path.exists(abs_image_path):
        return html.Div([
            html.P(f"❌ Image not found: {image_path}"),
            html.P("Simulation may still be generating images...")
        ])

    image_result = html.Img(
        src=f"/{image_path}",
        className="img",
        style={'border': '1px solid #e3e3e3', 'max-width': '100%'}
    )

    return html.Div([
        html.P(html.Div(f't={time} ({r_max} images available)')),
        html.P(image_result)
    ])


@app.server.route('/ViewerGL/<path:path>')
def static_file(path):
    static_folder = os.path.join(os.getcwd(), 'ViewerGL')
    return send_from_directory(static_folder, path)


if __name__ == "__main__":
    host = "127.0.0.3"
    port = 8050
    print(f"[INFO] ========================================")
    print(f"[INFO] Ansys Twin Builder (Real-time + ROM)")
    print(f"[INFO] ========================================")
    print(f"[INFO] Starting Dash server at http://{host}:{port}")
    print(f"[INFO] ✅ CSV caching enabled")
    print(f"[INFO] ✅ Real-time ROM visualization enabled")
    print(f"[INFO] ========================================")
    app.run_server(host=host, port=port, debug=False)
