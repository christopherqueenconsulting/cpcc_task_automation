/*
 * Program: Grade Book
 * Author: Eve Specimen
 * Purpose: Reads five test scores, then prints the average, the number of
 *          passing scores and the letter grade for the average.
 */
import java.util.Scanner;

public class GradeBook {
    // Number of scores to read
    public static final int SCORE_COUNT = 5;
    // Lowest score that counts as a pass
    public static final int PASSING_SCORE = 60;

    public static void main(String[] args) {
        Scanner input = new Scanner(System.in);
        int total = 0;
        int passing = 0;

        // Read each score, asking again until it is between 0 and 100
        for (int i = 1; i <= SCORE_COUNT; i++) {
            System.out.print("Enter score " + i + ": ");
            int score = input.nextInt();
            while (score < 0 || score > 100) {
                System.out.print("Score must be 0 to 100. Enter score " + i + ": ");
                score = input.nextInt();
            }
            total += score;
            if (score >= PASSING_SCORE) {
                passing++;
            }
        }

        // Average of the scores as a decimal number
        double average = (double) total / SCORE_COUNT;

        System.out.printf("Average: %.1f%n", average);
        System.out.println("Passing scores: " + passing);
        System.out.println("Letter grade: " + letterGrade(average));

        input.close();
    }

    // Returns the letter grade for an average
    public static String letterGrade(double average) {
        if (average >= 90) {
            return "A";
        } else if (average > 80) {
            return "B";
        } else if (average >= 70) {
            return "C";
        } else if (average >= 60) {
            return "D";
        }
        return "F";
    }
}
