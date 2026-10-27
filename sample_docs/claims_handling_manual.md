# Northwind Mutual: Claims Handling Manual (Sample)

This is a fictional manual for demonstrating DocQA. It does not describe any real insurer.

## 1. Purpose and Scope

This manual sets out how Northwind Mutual receives, reviews, pays and closes health insurance claims for members enrolled in the Standard and Plus plans. It applies to all claims examiners, team leads and the Special Investigations Unit (SIU). Dental and vision plans are administered by a third party and are out of scope.

## 2. Claim Intake

Claims can be submitted through the provider portal, by EDI 837 file, or on paper using form NM-100. Paper claims are scanned at the mailroom within two business days of receipt, and the scan date is recorded as the received date.

Every claim receives a 14-character claim reference at intake. The first two characters identify the channel: "PP" for provider portal, "ED" for EDI and "PA" for paper.

A claim is considered "clean" when it contains a valid member ID, a valid provider NPI, at least one diagnosis code, at least one procedure code, the date of service and the billed amount. Claims missing any of these elements are returned to the submitter with reason code R-12 and are not counted toward turnaround metrics until they are resubmitted.

## 3. Filing Deadlines

Providers must submit claims within 90 days of the date of service. Members who submit their own claims have 180 days from the date of service. Late claims are denied with reason code D-07 unless the submitter documents good cause, such as a natural disaster or a coordination-of-benefits delay with another insurer.

Corrected claims must be submitted within 60 days of the original claim's decision date.

## 4. Review Process

Clean claims under $2,500 are auto-adjudicated by the rules engine and do not require examiner review unless a rule flags them.

Claims of $2,500 or more, and any claim flagged by the rules engine, go to an examiner queue. Examiners must complete a first review within 5 business days of the claim entering the queue.

Claims over $25,000 require a second review by a team lead before payment is released. Claims over $100,000 also require sign-off from the Director of Claims.

## 5. Prior Authorization

Inpatient admissions, MRI and CT scans, and outpatient surgery require prior authorization. If a service that needs prior authorization is performed without it, the claim is pended and the provider has 30 days to submit retroactive authorization documents. If no documents arrive within 30 days, the claim is denied with reason code D-15.

Emergency services never require prior authorization.

## 6. Payment Turnaround

Northwind Mutual's target is to pay or deny 95% of clean claims within 30 calendar days of the received date. Clean claims not decided within 45 days accrue interest at 1% per month, paid to the provider together with the claim payment.

Payments are issued twice weekly, on Tuesdays and Fridays, by electronic funds transfer. Providers without EFT enrollment receive paper checks, which are mailed on Fridays only.

## 7. Fraud Indicators and SIU Referral

Examiners must refer a claim to the SIU when they observe any of the following:

- the same procedure billed for the same member more than three times in 30 days;
- services billed on a date after the member's recorded date of death;
- a provider billing more than 24 hours of timed services in a single day;
- documents that appear altered.

SIU referrals are made through the case management system using referral type "SIU-1". Examiners must not contact the provider about a claim once it has been referred to the SIU.

## 8. Appeals

Members and providers may appeal a denied claim within 180 days of the denial notice. First-level appeals are decided within 30 days by an examiner who was not involved in the original decision. If the first-level appeal is upheld, the appellant may request a second-level review by the Appeals Committee within 60 days. The Appeals Committee meets on the first Wednesday of each month.

Urgent appeals involving ongoing care are decided within 72 hours.

## 9. Overpayments and Recovery

When an overpayment is identified, the provider receives a recovery notice and has 45 days to repay or dispute it. Undisputed overpayments not repaid within 45 days are offset against future payments to that provider. Recovery is not pursued for overpayments under $25, which are written off.

## 10. Records Retention

Claim files, including attachments and correspondence, are retained for 10 years from the date the claim is closed. Files involving litigation or an SIU investigation are retained until the matter is resolved, plus 10 years.

## 11. Quality Assurance

Each month, team leads audit a random sample of 2% of each examiner's closed claims. The financial accuracy target is 99%, and the procedural accuracy target is 97%. Examiners below target for two consecutive months are placed on a coaching plan.
