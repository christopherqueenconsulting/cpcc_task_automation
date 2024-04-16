#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
import os
import tempfile
import zipfile
from typing import Tuple, List, Any

import streamlit as st
from langchain_openai import ChatOpenAI
from streamlit.runtime.uploaded_file_manager import UploadedFile

from cqc_cpcc.utilities.utils import read_file

CODE_LANGUAGES = [
    "abap", "abnf", "actionscript", "ada", "agda", "al", "antlr4", "apacheconf",
    "apex", "apl", "applescript", "aql", "arduino", "arff", "asciidoc", "asm6502",
    "asmatmel", "aspnet", "autohotkey", "autoit", "avisynth", "avroIdl", "bash",
    "basic", "batch", "bbcode", "bicep", "birb", "bison", "bnf", "brainfuck",
    "brightscript", "bro", "bsl", "c", "cfscript", "chaiscript", "cil", "clike",
    "clojure", "cmake", "cobol", "coffeescript", "concurnas", "coq", "cpp", "crystal",
    "csharp", "cshtml", "csp", "cssExtras", "css", "csv", "cypher", "d", "dart",
    "dataweave", "dax", "dhall", "diff", "django", "dnsZoneFile", "docker", "dot",
    "ebnf", "editorconfig", "eiffel", "ejs", "elixir", "elm", "erb", "erlang",
    "etlua", "excelFormula", "factor", "falselang", "firestoreSecurityRules", "flow",
    "fortran", "fsharp", "ftl", "gap", "gcode", "gdscript", "gedcom", "gherkin",
    "git", "glsl", "gml", "gn", "goModule", "go", "graphql", "groovy", "haml",
    "handlebars", "haskell", "haxe", "hcl", "hlsl", "hoon", "hpkp", "hsts", "http",
    "ichigojam", "icon", "icuMessageFormat", "idris", "iecst", "ignore", "inform7",
    "ini", "io", "j", "java", "javadoc", "javadoclike", "javascript", "javastacktrace",
    "jexl", "jolie", "jq", "jsExtras", "jsTemplates", "jsdoc", "json", "json5", "jsonp",
    "jsstacktrace", "jsx", "julia", "keepalived", "keyman", "kotlin", "kumir", "kusto",
    "latex", "latte", "less", "lilypond", "liquid", "lisp", "livescript", "llvm", "log",
    "lolcode", "lua", "magma", "makefile", "markdown", "markupTemplating", "markup",
    "matlab", "maxscript", "mel", "mermaid", "mizar", "mongodb", "monkey", "moonscript",
    "n1ql", "n4js", "nand2tetrisHdl", "naniscript", "nasm", "neon", "nevod", "nginx",
    "nim", "nix", "nsis", "objectivec", "ocaml", "opencl", "openqasm", "oz", "parigp",
    "parser", "pascal", "pascaligo", "pcaxis", "peoplecode", "perl", "phpExtras", "php",
    "phpdoc", "plsql", "powerquery", "powershell", "processing", "prolog", "promql",
    "properties", "protobuf", "psl", "pug", "puppet", "pure", "purebasic", "purescript",
    "python", "q", "qml", "qore", "qsharp", "r", "racket", "reason", "regex", "rego",
    "renpy", "rest", "rip", "roboconf", "robotframework", "ruby", "rust", "sas", "sass",
    "scala", "scheme", "scss", "shellSession", "smali", "smalltalk", "smarty", "sml",
    "solidity", "solutionFile", "soy", "sparql", "splunkSpl", "sqf", "sql", "squirrel",
    "stan", "stylus", "swift", "systemd", "t4Cs", "t4Templating", "t4Vb", "tap", "tcl",
    "textile", "toml", "tremor", "tsx", "tt2", "turtle", "twig", "typescript", "typoscript",
    "unrealscript", "uorazor", "uri", "v", "vala", "vbnet", "velocity", "verilog", "vhdl",
    "vim", "visualBasic", "warpscript", "wasm", "webIdl", "wiki", "wolfram", "wren", "xeora",
    "xmlDoc", "xojo", "xquery", "yaml", "yang", "zig"
]


@st.cache_data
def get_cpcc_css():
    # Embed custom fonts using HTML and CSS
    css = """
        <style>
            @font-face {
                font-family: "Franklin Gothic";
                src: url("https://db.onlinewebfonts.com/t/9c9dbb999dd7068f51335d93cc7328bd.eot");
                src: url("https://db.onlinewebfonts.com/t/9c9dbb999dd7068f51335d93cc7328bd.eot?#iefix")format("embedded-opentype"),
                url("https://db.onlinewebfonts.com/t/9c9dbb999dd7068f51335d93cc7328bd.woff2")format("woff2"),
                url("https://db.onlinewebfonts.com/t/9c9dbb999dd7068f51335d93cc7328bd.woff")format("woff"),
                url("https://db.onlinewebfonts.com/t/9c9dbb999dd7068f51335d93cc7328bd.ttf")format("truetype"),
                url("https://db.onlinewebfonts.com/t/9c9dbb999dd7068f51335d93cc7328bd.svg#Franklin Gothic")format("svg");
            }

            @font-face {
                font-family: 'ITC New Baskerville';
                src: url("https://db.onlinewebfonts.com/t/501ade6e29baa5c62c15ec28f3ed2c62.eot");
                src: url("https://db.onlinewebfonts.com/t/501ade6e29baa5c62c15ec28f3ed2c62.eot?#iefix")format("embedded-opentype"),
                url("https://db.onlinewebfonts.com/t/501ade6e29baa5c62c15ec28f3ed2c62.woff2")format("woff2"),
                url("https://db.onlinewebfonts.com/t/501ade6e29baa5c62c15ec28f3ed2c62.woff")format("woff"),
                url("https://db.onlinewebfonts.com/t/501ade6e29baa5c62c15ec28f3ed2c62.ttf")format("truetype"),
                url("https://db.onlinewebfonts.com/t/501ade6e29baa5c62c15ec28f3ed2c62.svg#ITC New Baskerville")format("svg");
            }

            body {
                font-family: 'Franklin Gothic', sans-serif;
            }

            h1, h2, h3, h4, h5, h6 {
                font-family: 'Franklin Gothic', sans-serif;
                font-weight: normal;
            }

            p {
                font-family: 'ITC New Baskerville', sans-serif;
                font-weight: normal;
            }
        </style>
        """
    return css


@st.cache_resource(hash_funcs={ChatOpenAI: id})
def get_custom_llm(temperature: float, model: str) -> ChatOpenAI:
    """
    This function returns a cached instance of ChatOpenAI based on the temperature and model.
    If the temperature or model changes, a new instance will be created and cached.
    """
    return ChatOpenAI(temperature=temperature, model=model, openai_api_key = st.session_state.openai_api_key)


def get_file_extension_from_filepath(file_path: str, remove_leading_dot: bool = False) -> str:
    basename = os.path.basename(file_path)
    file_name, file_extension = os.path.splitext(basename)
    if remove_leading_dot and file_extension.startswith("."):
        # st.info("Removing leading dot from file extension: " + file_extension)
        file_extension = file_extension[1:]

    if file_extension:
        file_extension = file_extension.lower()

    #st.info("Base Name: " + basename + " | File Name: " + file_name + " | File Extension : " + file_extension)

    return file_extension


def get_language_from_file_path(file_path):
    # Extract file extension from the file path
    file_extension = get_file_extension_from_filepath(file_path, True)

    # Check if the file extension exists in the mapping
    if file_extension in CODE_LANGUAGES:
        #st.info(file_extension + " | Found in CODE_LANGUAGES")
        return file_extension
    else:
        #st.info(file_extension + " | NOT Found in CODE_LANGUAGES")
        return None  # Return None if the file extension is not found


def define_code_language_selection(unique_key: str | int, default_option: str = 'java'):
    # List of available languages

    selected_language = st.selectbox(label="Select Code Language",
                                     key="language_select_" + unique_key,
                                     options=CODE_LANGUAGES,
                                     index=CODE_LANGUAGES.index(default_option))
    return selected_language


def define_chatGPTModel(unique_key: str | int, default_min_value: float = .2, default_max_value: float = .8,
                        default_temp_value: float = .2,
                        default_step: float = 0.1, default_option="gpt-3.5-turbo-16k-0613") -> Tuple[str, float]:
    # Dropdown for selecting ChatGPT models
    model_options = [default_option, "gpt-4-1106-preview"]
    selected_model = st.selectbox(label="Select ChatGPT Model",
                                  key="chat_select_" + unique_key,
                                  options=model_options,
                                  index=model_options.index(default_option))

    # Slider for selecting a value (ranged from 0.2 to 0.8, with step size 0.01)
    # Define the ranges and corresponding labels
    ranges = [(0, 0.3, "Low temperature: More focused, coherent, and conservative outputs."),
              (0.3, 0.7, "Medium temperature: Balanced creativity and coherence."),
              (0.7, 1, "High temperature: Highly creative and diverse, but potentially less coherent.")]

    temperature = st.slider(label="Chat GPT Temperature",
                            key="chat_temp_" + unique_key,
                            min_value=max(default_min_value, 0),
                            max_value=min(default_max_value, 1),
                            step=default_step, value=default_temp_value,
                            format="%.2f")

    # Determine the label based on the selected value
    for low, high, label in ranges:
        if low <= temperature <= high:
            st.write(label)
            break

    return selected_model, temperature


def add_upload_file_element(uploader_text: str, accepted_file_types: list[str], success_message: bool = True,
                            accept_multiple_files: bool = False) -> list[tuple[Any, str]] | tuple[Any, str]:
    uploaded_files = st.file_uploader(label=uploader_text, type=accepted_file_types,
                                      accept_multiple_files=accept_multiple_files)

    if accept_multiple_files:
        uploaded_file_paths = []
        for uploaded_file in uploaded_files:
            if uploaded_file is not None:
                # Get the original file name
                original_file_name = uploaded_file.name
                # Create a temporary file to store the uploaded file
                temp_file_name = upload_file_to_temp_path(uploaded_file)
                uploaded_file_paths.append((original_file_name, temp_file_name))
        if uploaded_files and success_message:
            st.success("File(s) uploaded successfully.")
        return uploaded_file_paths

    elif uploaded_files is not None:
        # Get the original file name
        original_file_name = uploaded_files.name
        # Create a temporary file to store the uploaded file
        temp_file_name = upload_file_to_temp_path(uploaded_files)
        if success_message:
            st.success("File uploaded successfully.")
        return original_file_name, temp_file_name

def upload_file_to_temp_path(uploaded_file: UploadedFile):
    file_extension = get_file_extension_from_filepath(uploaded_file.name)

    # Create a temporary file to store the uploaded instructions
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=file_extension)
    temp_file.write(uploaded_file.getvalue())
    # temp_file.close()

    return temp_file.name


def process_file(file_path, allowed_file_extensions):
    """ Using a file path determine if the file is a zip or single file and gives the contents back if single or dict mapping the studnet name and timestamp back to the combined contents"""

    # If it's a zip file
    if file_path.endswith('.zip'):
        with zipfile.ZipFile(file_path, 'r') as zip_file:
            folder_contents = {}
            for zip_info in zip_file.infolist():
                if any(zip_info.filename.lower().endswith(ext) for ext in allowed_file_extensions):
                    folder_path = os.path.dirname(zip_info.filename)
                    with zip_file.open(zip_info) as file:
                        file_contents = file.read()
                    folder_contents.setdefault(folder_path, []).append(file_contents)

            for folder_path, files in folder_contents.items():
                concatenated_contents = b''.join(files)
                print(f"Contents of folder '{folder_path}': {concatenated_contents.decode()}")

    # If it's a single file
    else:
        if any(file_path.lower().endswith(ext) for ext in allowed_file_extensions):
            with open(file_path, 'r') as file:
                print("Contents of single file:", file.read())


def on_download_click(file_path: str, button_label: str, download_file_name: str):
    file_mime_types = {
        ".java": "text/x-java-source",
        ".txt": "text/plain",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pdf": "application/pdf",
        ".zip": "application/zip"
    }
    file_extension = get_file_extension_from_filepath(download_file_name)
    mime_type = file_mime_types.get(file_extension, "application/octet-stream")
    st.info("file_extension: " + file_extension + " | mime_type: " + mime_type)

    file_content = read_file(file_path)

    st.info("file_path: "+file_path+" | download_file_name: "+download_file_name)
    st.markdown(file_content)

    # Trigger the download of the file
    st.download_button(label=button_label, data=file_content,
                       file_name=download_file_name, mime=mime_type, key=download_file_name)
