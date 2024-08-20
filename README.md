# CPCC Task Automation
A multi-page Streamlit app showcasing generative AI uses cases with LangChain, OpenAI, and others to help automate task for instructors at CPCC.

### Installation and Running the Project

#### Installation

1. **Clone the repository:**
   ```sh
   git clone <repository-url>
   cd <repository-directory>
   ```

2. **Install dependencies:**
   Make sure you have [Poetry](https://python-poetry.org/docs/#installation) installed. Then run:
   ```sh
   poetry install
   ```

#### Running the Project

1. **Run the application:**
   Use the provided `run.sh` script to start the application. You will be prompted to choose between running the app via Streamlit or Poetry.
   ```sh
   ./run.sh
   ```

2. **Follow the prompts:**
   - Select `streamlit` to run the app via Streamlit.
   - Select `poetry` to run the app via Poetry.


## Streamlit App

#### Take Attendance
* An app to take attendance using student activities (assignments, quizzes, discussions) in BrightSpace and records the attendance in MyColleges.

#### Give Feedback
* An app for giving students feedback on their project submissions.

#### Grade Exam
* An app for grading student exams using error definitions and a grading rubric.

*Provide the API keys and other required info in Settings, and select a use case from the sidebar to get started.*

#### Settings
* [OpenAI API Key](https://platform.openai.com/account/api-keys)  
* Instructor User ID
* Instructor Password
* Instructor Signature
* Attendance Tracker URL