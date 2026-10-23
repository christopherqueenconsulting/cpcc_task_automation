/*
 * Program: Sum To N
 * Author: Eve Specimen
 * Purpose: Adds the whole numbers from 1 to n.
 */
import java.util.Scanner;

public class SumToN {
    public static void main(String[] args) {
        Scanner input = new Scanner(System.in);

        // Read n from the user
        System.out.print("Enter n: ");
        int n = input.nextInt();

        // Stop when n is not a positive number
        if (n < 1) {
            System.out.println("Invalid");
        } else {
            // Add every number from 1 to n
            int sum = 0;
            for (int i = 1; i < n; i++) {
                sum = sum + i;
            }
            System.out.println("Sum: " + sum);
        }

        input.close();
    }
}
