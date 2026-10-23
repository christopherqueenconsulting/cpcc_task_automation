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
    while (hoursWorked < 0 || hoursWorked > MAX_HOURS) {
        cout << "Hours must be between 0 and 80. Enter hours worked: ";
        cin >> hoursWorked;
    }

    cout << "Enter hourly rate: ";
    cin >> hourlyRate;
    while (hourlyRate <= 0) {
        cout << "Rate must be greater than 0. Enter hourly rate: ";
        cin >> hourlyRate;
    }

    double grossPay = calculatePay(hoursWorked, hourlyRate);
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
