/*
 * Program: Even or Odd
 * Author: Eve Specimen
 * Purpose: Reads a whole number and reports whether it is even or odd.
 */
import java.util.Scanner;

public class EvenOdd {
    public static void main(String[] args) {
        Scanner input = new Scanner(System.in);

        // Read the number from the user
        System.out.print("Enter a whole number: ");
        int number = input.nextInt();

        // An even number has no remainder when divided by 2
        if (number % 2 == 1) {
            System.out.println("Even");
        } else {
            System.out.println("Odd");
        }

        input.close();
    }
}
