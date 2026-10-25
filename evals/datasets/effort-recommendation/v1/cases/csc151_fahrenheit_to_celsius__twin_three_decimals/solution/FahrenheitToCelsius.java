/*
 * Program: Temperature Converter
 * Author: Ada Example
 * Purpose: Converts a Fahrenheit temperature to Celsius.
 */
import java.util.Scanner;

public class FahrenheitToCelsius {
    // Fahrenheit reading at which water freezes
    public static final double FREEZING_POINT_F = 32.0;

    public static void main(String[] args) {
        Scanner input = new Scanner(System.in);

        // Read the Fahrenheit temperature
        System.out.print("Enter the temperature in Fahrenheit: ");
        double fahrenheit = input.nextDouble();

        // Convert using (F - 32) * 5 / 9
        double celsius = (fahrenheit - FREEZING_POINT_F) * 5.0 / 9.0;

        // Show the result with one decimal place
        System.out.printf("Celsius: %.1f%n", celsius);

        input.close();
    }
}
