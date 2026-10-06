#include <iostream>
#include <iomanip>
using namespace std;

const double OVERTIME_THRESHOLD = 40.0;
const double OVERTIME_MULTIPLIER = 1.5;
const double MAX_HOURS = 80.0;

double calculatePay(double hours, double rate);

int main() {
    double HW = 0.0;
    double R = 0.0;

    cout << "Enter hours worked: ";
    cin >> HW;
    while (HW < 0 || HW > MAX_HOURS) {
        cout << "Hours must be between 0 and 80. Enter hours worked: ";
        cin >> HW;
    }

    cout << "Enter hourly rate: ";
    cin >> R;
    while (R <= 0) {
        cout << "Rate must be greater than 0. Enter hourly rate: ";
        cin >> R;
    }

    double grossPay = calculatePay(HW, R);
    cout << fixed << setprecision(2);
    cout << "Gross pay: $" << grossPay << endl;

    return 0;
}

double calculatePay(double hours, double rate) {
    double pay = 0.0;
    if (hours > OVERTIME_THRESHOLD) {
        double overtimeHours = hours - OVERTIME_THRESHOLD;
        pay = (OVERTIME_THRESHOLD * rate) + (overtimeHours * rate * OVERTIME_MULTIPLIER);
    } else {
        pay = hours * rate;
    }
    return pay;
}
