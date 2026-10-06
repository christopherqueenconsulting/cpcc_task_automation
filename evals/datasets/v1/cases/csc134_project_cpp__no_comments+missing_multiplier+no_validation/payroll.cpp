#include <iostream>
#include <iomanip>
using namespace std;

const double OVERTIME_THRESHOLD = 40.0;
const double OVERTIME_MULTIPLIER = 1.5;
const double MAX_HOURS = 80.0;

double calculatePay(double hours, double rate);

int main() {
    double hoursWorked = 0.0;
    double hourlyRate = 0.0;

    cout << "Enter hours worked: ";
    cin >> hoursWorked;

    cout << "Enter hourly rate: ";
    cin >> hourlyRate;

    double grossPay = calculatePay(hoursWorked, hourlyRate);
    cout << fixed << setprecision(2);
    cout << "Gross pay: $" << grossPay << endl;

    return 0;
}

double calculatePay(double hours, double rate) {
    double pay = 0.0;
    if (hours > OVERTIME_THRESHOLD) {
        double overtimeHours = hours - OVERTIME_THRESHOLD;
        pay = (OVERTIME_THRESHOLD * rate) + (overtimeHours * rate);
    } else {
        pay = hours * rate;
    }
    return pay;
}
