import cqc_cpcc.attendance as AT
from cqc_cpcc.utilities.selenium_util import *


def test_bright_space_course_class():
    # From MyColleges find all courses belonging to instructor
    faculty_url = MYCOLLEGE_URL + "/Student/Student/Faculty"

    driver, wait = get_session_driver()

    # driver.get(faculty_url)

    # Wait for title to change
    # wait.until(EC.title_is("Web Login Service"))

    # Login
    # duo_login(driver)

    # Wait for title to change
    wait.until(EC.title_is(MYCOLLEGE_FACULTY_TITLE))

    bsc = AT.BrightSpace_Course("CSC-151-N804: Java Programming", "Spring", "2024", driver, wait)

def test_date_xpression():
    latest_post_dates = ["03-16-2024"]
    xpath_expression = "//tr[..//abbr[" + " or ".join(
        ["contains(.//text(), '{}')".format(d_date) for d_date in
         latest_post_dates]) + "]]//a[contains(@class,'d2l-linkheading-link')]"
    print(xpath_expression)



from langchain import LangChain

# Create a LangChain instance
lc = LangChain()



# Press the green button in the gutter to run the script.
if __name__ == '__main__':
    test_date_xpression()
    #test_bright_space_course_class()
