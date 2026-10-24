// Program: Rectangle Calculator
// Author: Dee Placeholder
// Purpose: Reads a length and width, then prints the area and perimeter.
#include <iostream>
#include <iomanip>
using namespace std;

int main() {
    double length = 0.0;
    double width = 0.0;

    // Read the two sides
    cout << "Enter the length: ";
    cin >> length;
    cout << "Enter the width: ";
    cin >> width;

    // Area is length times width; perimeter adds all four sides
    double area = length * width;
    double perimeter = length + width + length + width;

    // Show both results with two decimal places
    cout << "Area: " << area << endl;
    cout << "Perimeter: " << perimeter << endl;
    return 0;
}
