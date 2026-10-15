This project is a helper script for my personal use in my capacity as Vice President of my local BNI chapter. Each week I need to run reports and update the weekly meeting presentation with the results of those reports.

This script will be written in python and will use unofficial url endpoints to pull data from the BNI reporting website. It will use uv to manage a virtual enviroment for the project and to install dependencies.

Use requests to login and establish a session cookie.

Use pandas for table extraction from the returned report html.

Credentials for the BNI reporting website will be stored in a .env file and accessed using the python-dotenv package.

Do not attempt to use a single parser for all reports, their formats are all different. Develop a different parser for each report.

The script should generate and present the following pieces of information:
1. Upcoming Renewals - Use data from Summary Palms Report (`Ren. Date` Column)
A list of current members whose membership is up for renewal within the next 3 months. The list should include the members name. The list should be grouped by renweal month starting with the current month and ending with the month 3 months from now. The list should be sorted by renewal date within each month group.

Example Output:
```
Renewals This Month (March):
Bob Smith
Jane Doe

Renewals Next Month (April):
None

Renewals in May:
Rodger Fedora

Renewals in June:
None
```

2. VPs Stats Report for the Last Week. Use data from the Weekly Summary PALMS report.
Includes 3 pieces of information:
- Total Number of 1-2-1 Meetings
- Total Number of Referrals (This is internal / external combined)
- Total Closed Business for the week.


3. VPs Stats Report for the Last Month. Includes 3 pieces of information:
- Total Number of 1-2-1 Meetings
- Total Number of Referrals (This is internal / external combined)
- Total Closed Business for the month.
This information

4. Rolling Average TYFCB for the last 12 months.

5. All Time Chapter Stats
 - Total referrals given
 - Total Visitors recorded
 - Total 1-2-1s recorded
 - Total TYFCB recorded

The login page is https://www.bniconnectglobal.com/login/
the username and password fields are named username and password. You will need to use this page to generate a session cookie that can be used to access the reports.

Below are the reports that will be required.

Summary PALMS Report: https://www.bniconnectglobal.com/web/secure/reportsChapterPALMS?IdOrg=1&startDate=01/01/2025&endDate=03/31/2026&ShowDetails=false&__exports_details=1
Different to the weekly summary report. Used for rolling yearly totals.

Weekly Summary PALMS report: https://www.bniconnectglobal.com/web/secure/reportsChapterVP?IdOrg=1&Month=03&Year=2026
Can be used to generate the data for the VPs report for last week.
This report has a strange format using nested tables. The outer table contains 2 rows. The first row contains header cells. You are guaranteed to get `Member` and `Totals`. Depending on what time in the month this is run you will also get dates inbetween `Member` and `Totals` representing the elapsed weeks of the month. Each date header cell has a corresponding cell in the next outer table row that contains a sub table with all of the actual data in it. The row headings (member names) are always in the `Member` sub table with the `Ren. Date` renewal date data. The subtable for each of the date header cells and also the `Totals` subtable have individual columns. The columns are labeled:
GI = Referals Given Inside
GO = Referals Given Outside
RI = Referals Recieved Inside
RO = Referals Recieved Outside
GT = Referals Given Total
RT = Referals Recieved Total
A = Attendance
V = Visitors Invited
1-2-1s = Number of 1-2-1's participated in


Cumulative Summary PALMS Report: https://www.bniconnectglobal.com/web/secure/reportsChapterCumulativeVP?IdOrg=1&FromMonth=03&FromYear=2026&ToMonth=03&ToYear=2026
Can be used to generate the data for the VPs Report for Last Month

Once all data has been extracted and processed output a summary to the console with headings and the relevant focussed data.
