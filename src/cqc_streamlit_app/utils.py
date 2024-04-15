#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
import os
import tempfile
from typing import Tuple

import streamlit as st
from langchain_openai import ChatOpenAI


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
    return ChatOpenAI(temperature=temperature, model=model)


def define_code_language_selection(unique_key: str | int, default_option:str = 'java'):
    # List of available languages
    languages = [
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
    selected_language = st.selectbox(label="Select Code Language",
                                  key="language_select_" + unique_key,
                                  options=languages,
                                  index=languages.index(default_option))
    return selected_language


def define_chatGPTModel(unique_key: str | int, default_min_value: float = .2, default_max_value: float = .8,
                        default_temp_value: float = .2,
                        default_step: float = 0.1,  default_option = "gpt-3.5-turbo-16k-0613") -> Tuple[str, float]:
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


def add_upload_file_element(uploader_text: str, accepted_file_types: list[str], success_message: bool = True):
    uploaded_file = st.file_uploader(uploader_text, type=accepted_file_types)
    if uploaded_file is not None:
        file_extension = os.path.splitext(uploaded_file.name)[1]
        if success_message:
            st.success("File uploaded successfully.")
        # Create a temporary file to store the uploaded instructions
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=file_extension)
        temp_file.write(uploaded_file.getvalue())
        # temp_file.close()
        return temp_file.name
