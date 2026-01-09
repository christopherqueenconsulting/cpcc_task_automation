# Streamlit UI Package - cqc_streamlit_app

This document provides detailed documentation for the `src/cqc_streamlit_app/` package, which contains the multi-page Streamlit web interface for CPCC Task Automation.

## Overview

The `cqc_streamlit_app` package provides a user-friendly web interface for instructors to access all automation features without using the command line. Built with Streamlit, it offers interactive forms, file uploads, progress indicators, and result displays.

**Package Location**: `src/cqc_streamlit_app/`

**Key Responsibilities**:
- User interface for all automation features
- Form handling and input validation
- Progress visualization
- Results display and file downloads
- Settings and credential management

## Package Structure

```
src/cqc_streamlit_app/
├── Home.py                    # Main entry point (landing page)
├── pages/                     # Multi-page app routes
│   ├── 1_Take_Attendance.py   # Attendance automation UI
│   ├── 2_Give_Feedback.py     # Feedback generation UI
│   ├── 4_Grade_Assignment.py  # Exam grading UI
│   ├── 5_Find_Student.py      # Student lookup UI
│   └── 6_Settings.py          # Configuration and credentials
├── initi_pages.py             # Session state initialization
├── utils.py                   # UI utilities (CSS, formatting)
├── streamlit_logger.py        # UI-specific logger
├── pexels_helper.py           # Background image helper
└── README.md                  # Package-specific README
```

## Core Concepts

### Multi-Page Application

Streamlit automatically creates navigation from files in the `pages/` directory:
- Files prefixed with numbers (`1_`, `2_`, etc.) determine order
- File names are converted to page titles: `1_Take_Attendance.py` → "Take Attendance"
- Each page is an independent Python script with its own UI

### Session State

Streamlit uses `st.session_state` for data persistence across page reloads and interactions:
- Credentials (API keys, passwords)
- Settings (URLs, timeouts, flags)
- Temporary data (form inputs, processing state)

**Important**: Session state is **per-browser-session** - cleared when browser closes.

### Page Configuration

Each page should set configuration at the top:
```python
st.set_page_config(
    layout="wide",              # Use wide layout for more space
    page_title="Page Title",    # Browser tab title
    page_icon="📚"              # Emoji favicon
)
```

## Main Entry Point

### Home.py

**Purpose**: Landing page with project overview and quick links.

**Key Features**:
- Project description
- Feature highlights
- Navigation to other pages
- Getting started guide

**Structure**:
```python
# Page config
st.set_page_config(page_title="CPCC Task Automation", page_icon="🤖")

# Initialize session state
init_session_state()

# Apply custom CSS
st.markdown(get_cpcc_css(), unsafe_allow_html=True)

# Page content
st.title("CPCC Task Automation")
st.write("Welcome message...")

# Feature cards
col1, col2, col3 = st.columns(3)
with col1:
    st.subheader("📊 Take Attendance")
    # Description and link
```

## Page Modules

### 1_Take_Attendance.py

**Purpose**: UI for automated attendance tracking workflow.

**Key Features**:
- Date range selection (defaults to last 7 days, ending 2 days ago)
- Attendance tracker URL input
- Course selection (from MyColleges)
- Real-time progress indicator
- Results summary

**Workflow**:
1. User configures credentials in Settings (if not already set)
2. User navigates to Take Attendance page
3. User enters attendance tracker URL
4. User clicks "Take Attendance" button
5. System:
   - Logs into MyColleges
   - Retrieves course list
   - For each course, scrapes BrightSpace activities
   - Records attendance in MyColleges and tracker
6. User sees progress updates and final summary

**Key UI Elements**:
```python
# Form for inputs
with st.form("attendance_form"):
    attendance_url = st.text_input("Attendance Tracker URL", value=default_url)
    
    # Date range (optional override)
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("Start Date", value=default_start)
    with col2:
        end_date = st.date_input("End Date", value=default_end)
    
    submitted = st.form_submit_button("Take Attendance")
    
    if submitted:
        with st.spinner("Taking attendance..."):
            take_attendance(attendance_url)
        st.success("Attendance recorded successfully!")
```

**Error Handling**:
- Missing credentials → redirect to Settings page
- Invalid URL → show error message
- Timeout errors → display error with retry option

---

### 2_Give_Feedback.py

**Purpose**: UI for AI-powered project feedback generation.

**Key Features**:
- BrightSpace navigation helper
- Feedback type selection (Java, general programming)
- Rubric file upload
- Error definition upload (optional)
- Student selection (individual or all)
- Feedback preview
- Bulk download

**Workflow**:
1. User uploads rubric or error definitions
2. User navigates to BrightSpace submissions folder (guided by UI)
3. User selects feedback type (Java, general)
4. User clicks "Generate Feedback"
5. System:
   - Downloads each student submission
   - Parses code/documents
   - Sends to OpenAI for analysis
   - Generates Word documents with feedback
6. User previews and downloads feedback files

**Key UI Elements**:
```python
# File uploads
rubric_file = st.file_uploader("Upload Rubric (optional)", type=["docx", "txt"])
error_defs = st.file_uploader("Upload Error Definitions (optional)", type=["txt", "json"])

# Feedback type selection
feedback_type = st.selectbox(
    "Select Feedback Type",
    options=["General Programming", "Java Specific"]
)

# Generate button
if st.button("Generate Feedback"):
    with st.spinner("Generating feedback..."):
        for student in students:
            with st.spinner(f"Processing {student.name}..."):
                feedback = give_project_feedback(student, rubric, feedback_type)
                # Show progress
                st.progress((i + 1) / len(students))
    st.success(f"Generated feedback for {len(students)} students!")
```

**Cost Display**:
Shows estimated OpenAI API cost before processing:
```python
estimated_cost = len(students) * 0.10  # $0.10 per student estimate
st.info(f"Estimated API cost: ${estimated_cost:.2f}")
```

---

### 4_Grade_Assignment.py

**Purpose**: UI for automated exam grading with AI.

**Key Features**:
- Exam instructions upload
- Solution code upload
- Rubric upload
- Error definition generation or upload
- Batch grading interface
- Score summary and export

**Workflow**:
1. User uploads exam materials (instructions, solution, rubric)
2. User chooses to generate or upload error definitions
3. If generating: system uses AI to create error taxonomy
4. User reviews and approves error definitions
5. User navigates to submissions in BrightSpace
6. User clicks "Grade Exams"
7. System:
   - Downloads each submission
   - Applies error definitions
   - Calculates scores
   - Generates feedback reports
8. User reviews scores and exports to CSV

**Key UI Elements**:
```python
# Exam materials upload
col1, col2 = st.columns(2)
with col1:
    instructions = st.file_uploader("Exam Instructions", type=["pdf", "docx", "txt"])
with col2:
    solution = st.file_uploader("Solution Code", type=["java", "py", "txt"])

rubric = st.file_uploader("Grading Rubric", type=["docx", "txt"])

# Error definition handling
error_option = st.radio("Error Definitions", ["Generate with AI", "Upload"])
if error_option == "Generate with AI":
    if st.button("Generate Error Definitions"):
        with st.spinner("Analyzing exam and generating error definitions..."):
            error_defs = generate_error_definitions(instructions, solution, rubric)
        st.json(error_defs)
else:
    error_defs = st.file_uploader("Upload Error Definitions", type=["json"])

# Grading
if st.button("Grade Exams"):
    results = []
    for student in students:
        score = grade_submission(student, error_defs, rubric)
        results.append({"student": student.name, "score": score})
    
    # Display results
    st.dataframe(results)
    
    # Export option
    st.download_button(
        label="Download Results (CSV)",
        data=results_to_csv(results),
        file_name="exam_grades.csv",
        mime="text/csv"
    )
```

---

### 5_Find_Student.py

**Purpose**: UI for student lookup and information retrieval.

**Key Features**:
- Search by name or ID
- Course enrollment display
- Submission history
- Attendance pattern analysis

**Key UI Elements**:
```python
# Search input
search_term = st.text_input("Search Student", placeholder="Enter name or ID")

if st.button("Search"):
    student = find_student(search_term)
    
    if student:
        st.subheader(f"Student: {student.name}")
        
        # Tabs for different views
        tab1, tab2, tab3 = st.tabs(["Courses", "Submissions", "Attendance"])
        
        with tab1:
            st.dataframe(student.courses)
        with tab2:
            st.dataframe(student.submissions)
        with tab3:
            st.line_chart(student.attendance_data)
    else:
        st.warning("Student not found")
```

---

### 6_Settings.py

**Purpose**: Configuration and credential management.

**Key Features**:
- API key management (OpenAI)
- Instructor credentials (MyColleges/BrightSpace)
- Attendance tracker URL
- Feedback signature
- Advanced settings (timeouts, headless mode)

**Key UI Elements**:
```python
st.title("⚙️ Settings")

# API Keys section
st.subheader("API Keys")
openai_key = st.text_input(
    "OpenAI API Key",
    type="password",
    value=st.session_state.get('openai_api_key', ''),
    help="Get your API key from https://platform.openai.com/account/api-keys"
)

# Instructor credentials
st.subheader("Instructor Credentials")
userid = st.text_input("Instructor User ID", value=st.session_state.get('instructor_userid', ''))
password = st.text_input("Instructor Password", type="password")

# Attendance settings
st.subheader("Attendance Settings")
tracker_url = st.text_input("Attendance Tracker URL", value=st.session_state.get('attendance_tracker_url', ''))

# Save button
if st.button("Save Settings"):
    st.session_state['openai_api_key'] = openai_key
    st.session_state['instructor_userid'] = userid
    st.session_state['instructor_pass'] = password
    st.session_state['attendance_tracker_url'] = tracker_url
    
    # Also save to environment
    os.environ['OPENAI_API_KEY'] = openai_key
    
    st.success("Settings saved!")
```

**Security Notes**:
- Passwords use `type="password"` (masked input)
- Session state is browser-specific (not shared)
- Secrets stored in `.streamlit/secrets.toml` (not committed)
- Environment variables preferred for deployment

---

## Support Modules

### initi_pages.py

**Purpose**: Initialize session state with default values.

**Function**: `init_session_state()`

**Initializes**:
- API keys (from secrets or environment)
- Instructor credentials
- URLs and settings
- UI state (processing flags, temp data)

**Usage**:
```python
from cqc_streamlit_app.initi_pages import init_session_state

# At top of every page
init_session_state()
```

**Implementation Pattern**:
```python
def init_session_state():
    # Check if key exists, if not, set default
    if 'openai_api_key' not in st.session_state:
        st.session_state['openai_api_key'] = os.getenv('OPENAI_API_KEY', '')
    
    if 'instructor_userid' not in st.session_state:
        st.session_state['instructor_userid'] = os.getenv('INSTRUCTOR_USERID', '')
    
    # ... more initializations
```

---

### utils.py

**Purpose**: UI utilities for styling and formatting.

**Key Functions**:

#### `get_cpcc_css() -> str`
Returns custom CSS for CPCC branding (green/blue color scheme).

**Usage**:
```python
from cqc_streamlit_app.utils import get_cpcc_css

st.markdown(get_cpcc_css(), unsafe_allow_html=True)
```

**Customizations**:
- Primary color: CPCC green
- Secondary color: CPCC blue
- Button styling
- Header formatting
- Card layouts

#### Other Utilities
- `format_date(date) -> str` - Format dates for display
- `format_currency(amount) -> str` - Format API costs
- `validate_url(url) -> bool` - Validate URLs

---

### streamlit_logger.py

**Purpose**: UI-specific logging that integrates with Streamlit.

**Differences from main logger**:
- Shows log messages in Streamlit UI (using `st.info()`, `st.warning()`, `st.error()`)
- Still logs to file for audit trail
- User-friendly formatting

**Usage**:
```python
from cqc_streamlit_app.streamlit_logger import logger

logger.info("Starting attendance process")  # Shows in UI and logs to file
logger.error("Failed to connect to BrightSpace")  # Shows error in UI
```

---

### pexels_helper.py

**Purpose**: Helper for fetching background images from Pexels API.

**Usage**: Provides visual enhancements to landing page.

---

## UI/UX Patterns

### Form Pattern
Use forms for grouped inputs with single submit:
```python
with st.form("my_form"):
    input1 = st.text_input("Field 1")
    input2 = st.selectbox("Field 2", options)
    submitted = st.form_submit_button("Submit")
    
    if submitted:
        # Process form
        st.success("Done!")
```

### Progress Indication
Always show progress for long operations:
```python
with st.spinner("Processing..."):
    for i, item in enumerate(items):
        process(item)
        st.progress((i + 1) / len(items))

st.success("Completed!")
```

### Error Display
```python
try:
    result = risky_operation()
    st.success("Success!")
except Exception as e:
    st.error(f"Error: {str(e)}")
    logger.error(f"Operation failed: {e}", exc_info=True)
```

### Conditional Display
```python
if st.session_state.get('openai_api_key'):
    st.write("API key configured ✓")
    # Show main functionality
else:
    st.warning("Please configure OpenAI API key in Settings")
    if st.button("Go to Settings"):
        st.switch_page("pages/6_Settings.py")
```

### Tabs for Organization
```python
tab1, tab2, tab3 = st.tabs(["Upload", "Configure", "Results"])

with tab1:
    # Upload UI
    
with tab2:
    # Configuration UI
    
with tab3:
    # Results display
```

### File Upload
```python
uploaded_file = st.file_uploader("Upload file", type=["docx", "txt"])
if uploaded_file:
    content = uploaded_file.read()
    # Process content
```

### Download Results
```python
result_data = generate_results()
st.download_button(
    label="Download Results",
    data=result_data,
    file_name="results.txt",
    mime="text/plain"
)
```

## Integration with Core Logic

### Calling Automation Functions

Pages import and call functions from `cqc_cpcc`:

```python
from cqc_cpcc.attendance import take_attendance
from cqc_cpcc.project_feedback import give_project_feedback

# In page
if st.button("Take Attendance"):
    try:
        with st.spinner("Taking attendance..."):
            take_attendance(url)
        st.success("Attendance taken!")
    except Exception as e:
        st.error(f"Failed: {e}")
```

### Error Handling

Always wrap automation calls in try-except:
- Catch exceptions from core logic
- Display user-friendly error messages
- Log full error details for debugging
- Provide recovery options (retry, reset)

### State Management

Don't store large objects in session state:
```python
# BAD
st.session_state['all_students'] = get_all_students()  # Large list

# GOOD
students = get_all_students()  # Local variable, not persisted
```

## Styling and Branding

### CPCC Colors
- Primary: `#00703C` (CPCC Green)
- Secondary: `#0078AE` (CPCC Blue)
- Accent: `#F7941D` (Orange)

### Consistent Layout
All pages use:
- Wide layout (`layout="wide"`)
- Custom CSS from `get_cpcc_css()`
- Similar header structure
- Consistent button styling

### Responsive Design
Streamlit handles most responsiveness, but consider:
- Use `st.columns()` for side-by-side elements
- Avoid fixed widths
- Test on different screen sizes

## Testing Considerations

### Manual Testing Primary
Streamlit UI is difficult to unit test automatically:
- Manual testing is primary validation method
- Test happy path AND error cases
- Test with empty inputs, large inputs, invalid inputs
- Test with various screen sizes

### What to Test
- Form submission with valid/invalid data
- File uploads (various formats)
- Error handling (disconnect, timeout)
- Settings persistence
- Navigation between pages

### Integration Testing
Test that UI correctly calls core logic:
```python
# In integration test
def test_attendance_page_calls_core_logic(mocker):
    mock_take_attendance = mocker.patch('cqc_cpcc.attendance.take_attendance')
    
    # Simulate page interaction
    # ...
    
    # Verify core logic was called
    mock_take_attendance.assert_called_once()
```

## Performance Considerations

### Minimize Reruns
Streamlit reruns entire script on each interaction:
- Use `@st.cache_data` for expensive computations
- Use `@st.cache_resource` for objects (drivers, models)
- Don't perform automation in page body (only on button click)

### Caching Pattern
```python
@st.cache_data(ttl=3600)  # Cache for 1 hour
def load_course_list():
    # Expensive operation
    return courses

courses = load_course_list()  # Only runs once per hour
```

### Large Data Display
```python
# For large datasets, use pagination or filtering
st.dataframe(data.head(100))  # Show first 100 rows
```

## Deployment Considerations

### Secrets Management
For production deployment:
- Use `.streamlit/secrets.toml` for local dev
- Use environment variables for cloud deployment
- Never commit secrets to git

### Environment Variables
Set these in deployment environment:
- `OPENAI_API_KEY`
- `INSTRUCTOR_USERID`
- `INSTRUCTOR_PASS`
- `ATTENDANCE_TRACKER_URL`

### Docker Deployment
```bash
docker-compose up
```

Requires `.env` file with secrets.

### Cloud Deployment
Streamlit Community Cloud:
1. Connect GitHub repository
2. Configure secrets in dashboard
3. Deploy from branch

## Known Limitations

1. **Session State Not Persistent**: Cleared on browser close (no database)
2. **Single User**: No multi-user support (coming in future)
3. **No Authentication**: Assumes trusted users (instructor credentials required)
4. **Manual 2FA**: Duo push requires manual approval (can't be automated)
5. **File Upload Size**: Limited by Streamlit (200 MB max)

## Troubleshooting

### Common Issues

**Issue**: Settings not persisting
- **Cause**: Browser session cleared or cookies disabled
- **Solution**: Re-enter settings, use `.streamlit/secrets.toml` for defaults

**Issue**: Blank page or errors on startup
- **Cause**: Missing dependencies or import errors
- **Solution**: Check `poetry install` ran successfully, view console for errors

**Issue**: Slow page loads
- **Cause**: Expensive computations in page body
- **Solution**: Move to cached functions or button click handlers

**Issue**: File upload fails
- **Cause**: File too large or wrong format
- **Solution**: Check file size (<200 MB), verify format matches allowed types

## Future Enhancements

1. **User Authentication** - Secure login for multiple instructors
2. **Database Integration** - Persist settings and history
3. **Real-Time Updates** - WebSocket for live progress updates
4. **Mobile Optimization** - Better responsive design for tablets/phones
5. **Batch Operations** - Queue multiple courses for processing
6. **Analytics Dashboard** - Student progress tracking and insights
7. **Email Integration** - Send feedback directly to students

## Related Documentation

- [src-cqc-cpcc.md](src-cqc-cpcc.md) - Core logic called by UI
- [utilities.md](utilities.md) - Utilities used in UI
- [ARCHITECTURE.md](ARCHITECTURE.md) - Overall system design

---

*For questions or clarifications, see [docs/README.md](README.md) or open a GitHub issue.*
