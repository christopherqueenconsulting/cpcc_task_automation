#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
import pandas as pd
import streamlit as st

from cqc_streamlit_app.initi_pages import init_session_state
from cqc_streamlit_app.utils import get_cpcc_css

# Initialize session state variables
init_session_state()

GR_CRITERIA = "Criteria"
GR_PPL = "Possible Points Loss"


def define_grading_rubric():
    st.header("Define Feedback Types")

    # Preload the table with default rows and values
    default_data = [
        {GR_CRITERIA: "Flowgorithm contains errors, will not run", GR_PPL: 50},
        {GR_CRITERIA: "Failure to calculate the correct answers", GR_PPL: 25},
        {GR_CRITERIA: "No comment block containing name, date and purpose", GR_PPL: 10},
        {GR_CRITERIA: "Failure to meet lab requirements", GR_PPL: 10},
        {GR_CRITERIA: "Inappropriate choice of data types", GR_PPL: 10},
        {GR_CRITERIA: "Failure to utilize constants, when appropriate, for program values", GR_PPL: 10},
        {GR_CRITERIA: "Lack of clear, succinct input prompts for data", GR_PPL: 10},
        {GR_CRITERIA: "Lack of clear, descriptive labels for output data", GR_PPL: 10},
        {GR_CRITERIA: '"hard-coding" numbers in calculations', GR_PPL: 10},
        {GR_CRITERIA: "Failure to include student name as part of flowgorithm file", GR_PPL: 5},
        {GR_CRITERIA: "Failure to include flowgorithm lab number as part of flowgorithm file name", GR_PPL: 5},
    ]
    grading_rubric_df = pd.DataFrame(default_data)

    # Allow users to edit the table
    edited_df = st.data_editor(grading_rubric_df, key='grading_rubric', hide_index=True,
                               num_rows="dynamic",
                               column_config={
                                   'Name': st.column_config.TextColumn(GR_CRITERIA + ' (required)', required=True),
                                   'Description': st.column_config.TextColumn(GR_PPL + ' (required)', required=True)
                               }
                               )  # 👈 An editable dataframe

    return edited_df


def dict_to_markdown_table(data, headers):
    # Create the header row
    markdown_table = "| " + " | ".join(headers) + " |\n"
    markdown_table += "| " + " | ".join(["-" * len(header) for header in headers]) + " |\n"

    # Iterate over the dictionary items and add rows to the table
    for row_data in data:
        markdown_table += "| " + " | ".join([str(row_data.get(header, '')) for header in headers]) + " |\n"

    return markdown_table


def main():
    # TODO: Initialize other session state variables - the ones you need in .env

    st.set_page_config(page_title="Grade Assignment", page_icon="🦜️🔗")  # TODO: Change the page icon

    css = get_cpcc_css()
    st.markdown(
        css,
        unsafe_allow_html=True
    )

    st.markdown("""Here we will give feedback and grade a students assignment submission""")

    # Add elements to page to work with

    st.header("Assignment Instructions")
    assignment_instructions = st.text_area("Enter assignment Instructions")

    # Add grading rubric
    grading_rubric = define_grading_rubric()
    grading_rubric_dict = {}
    for _, row in grading_rubric.iterrows():
        criteria = row[GR_CRITERIA]
        ppl = row[GR_PPL]
        grading_rubric_dict[criteria] = ppl

    # Write the Markdown table to a Streamlit textarea
    st.text_area("Grading Rubric", grading_rubric)

    # Example header list
    headers = [GR_CRITERIA, GR_PPL]
    # Convert the dictionary to a Markdown table
    markdown_table = dict_to_markdown_table(grading_rubric, headers)

    # Write the Markdown table to a Streamlit textarea
    st.text_area("Markdown Table", markdown_table)

    st.header("Assignment Total Points Possible")
    total_points_possible = st.text_input("Enter total points possible for this assignment", "50")


if __name__ == '__main__':
    main()
