/*
 * Program: Access Check
 * Author: Cal Fixture
 * Purpose: Checks that a code is long enough and contains a digit.
 */
import java.util.Scanner;

public class AccessCheck {
    // Shortest code that is accepted
    public static final int MIN_LENGTH = 8;

    public static void main(String[] args) {
        Scanner input = new Scanner(System.in);

        // Read the access code
        System.out.print("Enter an access code: ");
        String code = input.nextLine();

        // Look for at least one digit
        boolean hasDigit = false;
        for (char ch : code.toCharArray()) {
            if (Character.isDigit(ch)) {
                hasDigit = true;
            }
        }

        // The code needs the minimum length and a digit
        if (code.length() >= MIN_LENGTH && hasDigit) {
            System.out.println("Valid");
        } else {
            System.out.println("Invalid");
        }

        input.close();
    }
}
