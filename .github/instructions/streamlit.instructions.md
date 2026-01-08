# Streamlit UI Instructions

**Applies to:** `src/cqc_streamlit_app/**/*.py`

## Application Structure

### Multi-Page App
- `Home.py` - Main entry point (landing page)
- `pages/` - Individual feature pages:
  - `1_Take_Attendance.py` - Attendance automation UI
  - `2_Give_Feedback.py` - Feedback generation UI
  - `4_Grade_Assignment.py` - Exam grading UI
  - `5_Find_Student.py` - Student lookup UI
  - `6_Settings.py` - Configuration and credentials

### Naming Convention
- Page files prefixed with numbers for ordering: `1_`, `2_`, etc.
- Names are converted to navigation: `1_Take_Attendance.py` → "Take Attendance"

## Streamlit Patterns

### Session State
- **Always initialize state** using `init_session_state()` from `initi_pages.py`
- Store credentials, API keys, settings in `st.session_state`
- Example:
```python
from cqc_streamlit_app.initi_pages import init_session_state

init_session_state()  # Call at top of page

# Access state
if st.session_state.get('openai_api_key'):
    # API key is set
```

### Page Configuration
Set at the top of each page file:
```python
st.set_page_config(
    layout="wide",  # Use wide layout for better space
    page_title="Page Title",
    page_icon="📚"  # Use relevant emoji
)
```

### Styling
- Use `get_cpcc_css()` from `utils.py` for consistent styling
- Apply with `st.markdown(css, unsafe_allow_html=True)`
- CPCC branding: Green/blue color scheme

### Form Patterns
```python
with st.form("my_form"):
    input1 = st.text_input("Label")
    input2 = st.selectbox("Dropdown", options=["A", "B"])
    submitted = st.form_submit_button("Submit")
    
    if submitted:
        # Process form
        st.success("Done!")
```

### Long-Running Operations
```python
with st.spinner("Processing..."):
    result = long_running_function()

st.success("Completed!")
```

### Error Display
```python
try:
    result = risky_operation()
    st.success("Success!")
except Exception as e:
    st.error(f"Error: {str(e)}")
    logger.error(f"Error in operation: {e}", exc_info=True)
```

## Integration with Core Logic

### Calling Automation Functions
```python
from cqc_cpcc.attendance import take_attendance
from cqc_cpcc.project_feedback import give_project_feedback

# In Streamlit page
if st.button("Take Attendance"):
    try:
        with st.spinner("Taking attendance..."):
            take_attendance(url)
        st.success("Attendance taken!")
    except Exception as e:
        st.error(f"Failed: {e}")
```

### Environment Variables
- Use `st.secrets` for sensitive data (API keys, passwords)
- Configure in `.streamlit/secrets.toml` (not committed)
- Access: `st.secrets["OPENAI_API_KEY"]`

### Settings Management
- Store settings in session state
- Persist to secrets or environment on save
- Validate settings before use (API key format, URL validity)

## UI/UX Guidelines

### Layout
- Use `st.columns()` for side-by-side elements
- Use `st.expander()` for collapsible sections
- Use `st.tabs()` for tabbed content
- Keep forms concise (group related fields)

### Feedback to User
- Always show **progress** for long operations (spinner, progress bar)
- Display **success messages** on completion
- Show **error messages** with context (not just "Error")
- Log to file AND display in UI

### Input Validation
- Validate inputs before processing
- Show validation errors inline
- Disable submit button until valid
- Provide helpful placeholder text

### Data Display
- Use `st.dataframe()` for tabular data (interactive)
- Use `st.table()` for small static tables
- Use `st.json()` for structured data
- Use `st.code()` for code snippets

### File Handling
```python
uploaded_file = st.file_uploader("Upload file", type=["docx", "txt"])
if uploaded_file:
    content = uploaded_file.read()
    # Process content
```

## Best Practices

### Performance
- **Cache expensive operations** with `@st.cache_data` or `@st.cache_resource`
- Don't rerun automation on every widget interaction
- Use `st.rerun()` sparingly (triggers full page refresh)

### State Management
- Keep minimal state (don't store large objects)
- Clear state after operations complete
- Use unique keys for widgets in loops: `key=f"widget_{i}"`

### Error Handling
- Catch exceptions from core logic
- Display user-friendly messages (not stack traces)
- Log full errors to file for debugging
- Provide recovery options (reset, retry)

### Security
- Never display API keys or passwords in UI
- Validate user inputs (SQL injection, XSS even though Streamlit escapes)
- Don't log sensitive information
- Use secrets management

### Testing
- Manual testing is primary (Streamlit is hard to unit test)
- Test happy path AND error cases
- Test with various screen sizes (responsive layout)
- Test with empty inputs, large inputs, invalid inputs

## Common Streamlit Patterns

### Conditional Rendering
```python
if st.session_state.get('authenticated'):
    st.write("Welcome!")
else:
    st.warning("Please configure settings first")
```

### Dynamic Lists
```python
if 'items' not in st.session_state:
    st.session_state.items = []

new_item = st.text_input("Add item")
if st.button("Add"):
    st.session_state.items.append(new_item)
    st.rerun()

for i, item in enumerate(st.session_state.items):
    st.write(f"{i+1}. {item}")
```

### Download Results
```python
result_data = process_data()
st.download_button(
    label="Download Results",
    data=result_data,
    file_name="results.txt",
    mime="text/plain"
)
```

## Integration Points

- **Logger**: Import from `cqc_streamlit_app.streamlit_logger` (UI-specific logger)
- **Utilities**: Use `utils.py` for UI utilities (CSS, formatting)
- **Core Logic**: Import from `cqc_cpcc.*` for automation functions
- **State Init**: Always use `initi_pages.py` for consistent state setup
