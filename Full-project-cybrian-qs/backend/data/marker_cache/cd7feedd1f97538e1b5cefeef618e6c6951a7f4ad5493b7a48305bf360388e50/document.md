| Document Title  | SOP for AI-Based Batch Release<br>Monitoring | SOP No.      | SOP-QA-AI-001                           |
|-----------------|----------------------------------------------|--------------|-----------------------------------------|
| Department      | Quality Assurance / Digital<br>Quality       | Version      | 1.0                                     |
| Effective Date  | To be assigned                               | Review Date  | Every 24 months                         |
| Confidentiality | Internal Controlled Document                 | Page Control | Page numbering applied in PDF<br>viewer |

# STANDARD OPERATING PROCEDURE

### AI-Based Batch Release Monitoring

| Role        | Name / Title               | Signature | Date |  |
|-------------|----------------------------|-----------|------|--|
| Prepared by | Quality Systems Specialist |           |      |  |
| Reviewed by | QA Manager                 |           |      |  |
| Reviewed by | IT Validation Lead         |           |      |  |
| Approved by | Head of Quality            |           |      |  |

## **1. Purpose**

This SOP defines the controlled process for using an AI-based batch release monitoring system to support Quality Assurance review of manufacturing batch records, process parameters, deviations, environmental monitoring data, laboratory results, and release readiness indicators. The system is intended to assist decision-making and does not replace the responsibility of authorized Quality personnel.

## **2. Scope**

This procedure applies to pharmaceutical manufacturing batches where AI-enabled monitoring is used to identify release risks, highlight missing records, compare batch data against approved limits, and support batch disposition review. It applies to QA, Production, QC, Engineering, IT, and Digital Quality teams involved in data review and batch release support.

# **3. Responsibility**

All users must follow approved procedures, maintain data integrity, and ensure that AI-generated outputs are reviewed by qualified personnel before use in any quality decision.

| Role                  | Responsibility                                                        |  |
|-----------------------|-----------------------------------------------------------------------|--|
| QA Reviewer           | Reviews AI alerts, confirms batch record completeness, evaluates      |  |
|                       | release risks, and documents final QA assessment.                     |  |
| QA Manager            | Approves release recommendation, reviews unresolved critical alerts,  |  |
|                       | and ensures deviations/CAPAs are created where required.              |  |
|                       | Ensures batch execution data is complete, accurate, and available for |  |
| Production Supervisor | AI monitoring.                                                        |  |
|                       | Ensures laboratory results are approved and any OOS/OOT events        |  |
| QC Analyst            | are linked to the batch record.                                       |  |
|                       | Maintains validated state of the AI system, access controls, audit    |  |
| IT Validation Lead    | trails, backup, and change control.                                   |  |
|                       | Reviews data mapping, master data quality, and integration            |  |
| Data Steward          | completeness.                                                         |  |

## **4. Definitions**

The following terms are used within this SOP.

| Term                | Definition                                                                                                               |
|---------------------|--------------------------------------------------------------------------------------------------------------------------|
| AI Batch Monitoring | Use of artificial intelligence or rules-assisted analytics to review batch<br>data and highlight possible release risks. |
| Batch Release       | Formal QA decision confirming whether a manufactured batch may be<br>released for distribution.                          |

| Critical Alert   | A system-generated warning indicating a possible direct impact on product quality, patient safety, or compliance.                     |  |
|------------------|---------------------------------------------------------------------------------------------------------------------------------------|--|
| Data Integrity   | Complete, consistent, accurate, attributable, legible, contemporaneous, original, and available data throughout the record lifecycle. |  |
| Human Review     | Assessment by an authorized person before accepting, rejecting, or acting on AI-generated output.                                     |  |
| Validated System | A computerized system that has documented evidence proving it is fit for its intended use.                                            |  |

### 5. Procedure Overview

The AI batch monitoring process shall be executed only on validated data sources and approved master data. The system shall generate alerts and release readiness indicators, but final release authority remains with Quality Assurance.

#### 5.1 Batch Data Intake

The system receives batch record data, manufacturing parameters, laboratory results, deviation records, CAPA status, environmental monitoring results, equipment status, and audit trail indicators from approved source systems. Data transfer shall be automatic where possible and manually verified when automatic transfer is not available.

### **5.2 Data Completeness Check**

The system checks whether all required data elements are available before batch release review begins. Missing or incomplete data shall be flagged as open items and reviewed by QA.

#### **5.3 Al Alert Generation**

The system compares batch data against approved limits, prior batch behavior, master batch records, and predefined quality rules. Alerts shall be categorized as critical, major, minor, or informational.

### 5.4 Human Review of Al Output

QA shall review each Al-generated alert and determine whether it is valid, false positive, duplicate, or requires further investigation. No batch shall be released based only on an automated Al recommendation.

#### 5.5 Release Readiness Decision

A batch may be recommended for release only when all critical alerts are closed, all required records are complete, QC results are approved, deviations are assessed, and QA review is documented.

#### **5.6 Exception Handling**

If the AI system is unavailable, QA may proceed using the approved manual batch review process. The outage shall be documented, and missed AI monitoring shall be assessed after restoration.

| Step | Activity                                | Input                             | Output                    | Responsible Role            |
|------|-----------------------------------------|-----------------------------------|---------------------------|-----------------------------|
| 1    | Import batch data from approved systems | Batch record, QC data, deviations | Batch data package        | System / Data Steward       |
| 2    | Run completeness and integrity checks   | Required data checklist           | Missing data report       | System                      |
| 3    | Generate AI alerts and risk score       | Process and quality data          | Alert list and risk score | System                      |
| 4    | Review AI alerts                        | Alert list, batch record          | QA decision per alert     | QA Reviewer                 |
| 5    | Create deviation/CAPA if needed         | Confirmed quality issue           | Deviation or CAPA record  | QA Reviewer / QA<br>Manager |
| 6    | Perform final release assessment        | Reviewed batch package            | Release recommendation    | QA Manager                  |
| 7    | Approve or reject batch release         | Release recommendation            | Batch disposition         | Authorized QA Person        |

| Alert Category | Example Condition               | Required Action             | Release Impact                 |
|----------------|---------------------------------|-----------------------------|--------------------------------|
| Critical       | Sterilization parameter outside | Immediate QA escalation and | Batch cannot be released until |
|                | approved limit                  | deviation required          | resolved                       |
| Major          | Missing approved QC result or   | QA review and documented    | Release blocked until          |
|                | open deviation                  | justification required      | assessment complete            |
| Minor          | Minor documentation mismatch    | Correction or documented    | Release may proceed after QA   |
|                | without quality impact          | explanation                 | acceptance                     |

| Informational | Trend warning within approved<br>limits | Monitor and record as applicable | No direct release block |
|---------------|-----------------------------------------|----------------------------------|-------------------------|

# **6. Acceptance Criteria**

The following minimum conditions shall be met before AI-supported release recommendation can be accepted.

| No. | Acceptance Criterion                                       | Evidence Required                                  |
|-----|------------------------------------------------------------|----------------------------------------------------|
| 1   | All mandatory batch record sections are<br>complete        | Completed electronic or scanned batch<br>record    |
| 2   | All QC test results are approved                           | Approved certificate of analysis or LIMS<br>record |
| 3   | No unresolved critical AI alerts remain                    | Closed alert log with QA decision                  |
| 4   | All deviations are assessed for batch impact               | Deviation impact assessment                        |
| 5   | Required CAPAs are initiated or linked where<br>applicable | CAPA reference or justification                    |
| 6   | Audit trail review is complete for critical data           | Audit trail review record                          |
| 7   | Final release decision is documented by<br>authorized QA   | QA release checklist and approval                  |

# **7. Data Integrity and Compliance Controls**

All AI monitoring activities shall comply with applicable GMP data integrity principles. Source data must remain traceable to the original system of record, and AI-generated outputs must be retained with timestamps, user actions, and audit trail references.

| Control Area   | Requirement                                                                         | Verification Method             |
|----------------|-------------------------------------------------------------------------------------|---------------------------------|
| Access Control | Only authorized users may view, review, or<br>close AI alerts                       | Role-based access review        |
| Audit Trail    | System must capture data import, alert<br>generation, review decisions, and changes | Periodic audit trail review     |
| Traceability   | Each alert must link to source batch data or<br>rule condition                      | Alert-to-source reference check |
| Data Retention | AI outputs and review decisions must be<br>retained according to record policy      | Archive and backup review       |
| Change Control | Model, rule, threshold, or integration changes<br>require approval                  | Approved change control record  |

# **8. System Validation and Periodic Review**

The AI batch release monitoring system shall be validated before production use and maintained in a controlled state. Any model update, rule change, integration change, or infrastructure change shall be assessed under change control.

# **9. Deviation and CAPA Handling**

Confirmed AI alerts with potential quality impact shall be documented through the approved deviation process. Repeated alert patterns shall be evaluated for CAPA or continuous improvement action.

### **10. Records**

All records generated under this SOP shall be maintained according to the approved document retention policy.

| Record                  | Owner         | Retention Location           | Retention Period                          |
|-------------------------|---------------|------------------------------|-------------------------------------------|
| AI alert log            | QA            | AI monitoring system / QMS   | As per GMP record policy                  |
| Batch release checklist | QA            | QMS / Batch record system    | As per batch record policy                |
| Deviation/CAPA records  | QA            | QMS                          | As per QMS policy                         |
| Validation evidence     | IT Validation | Validation repository        | Lifecycle of system + retention<br>period |
| Audit trail review      | QA / IT       | QMS or controlled repository | As per data integrity policy              |

## **11. Training**

All users shall be trained on this SOP, relevant system procedures, data integrity expectations, and their role-specific responsibilities before using the AI batch monitoring system.

# **12. References**

Applicable GMP regulations, site quality manual, computerized system validation procedure, data integrity procedure, deviation management procedure, CAPA procedure, and batch release procedure shall be followed.

## **13. Revision History**

Revision history shall be maintained for every approved version of this SOP.

| Version | Effective Date | Change Summary                                         | Approved By     |
|---------|----------------|--------------------------------------------------------|-----------------|
| 1.0     | To be assigned | Initial issue for AI-based batch<br>release monitoring | Head of Quality |