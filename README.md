# BIAI CRM (Round 2, Task 1)

A small CRM for BIAI's enquiries (WhatsApp, Facebook, website, phone notes).
AI reads a pasted message, fills in the details, and suggests a reply in the sender's language.

**Live demo:** https://biai-crm-trino.streamlit.app/  |  **Video:** `<paste the Loom link>`

![System design](docs/architecture.png)

## Screens
1. **Pipeline** – New, Contacted, Interested, Paid, Lost. Badges for high-value, repeat askers and "waiting 48h+". One-click "Contacted".
2. **Triage** – urgent, support, job seekers, school students, spam. Kept out of the sales pipeline.
3. **Add enquiry** – paste a message, AI fills the form and writes a reply (English, Banglish or Bangla).
4. **Weekly summary** – numbers counted by code, sentences written by AI, numbers checked by code afterwards.

### Screenshots

<table align="center">
  <tr>
    <td align="center" width="50%">
      <img src="CRM.png" alt="CRM Pipeline" width="100%" /><br />
      <b>CRM Pipeline</b>
    </td>
    <td align="center" width="50%">
      <img src="Lead_English_Reply.png" alt="Lead English Reply" width="100%" /><br />
      <b>Lead English Reply</b>
    </td>
  </tr>
  <tr>
    <td align="center" width="50%">
      <img src="Lead_Banglish_Reply.png" alt="Lead Banglish Reply" width="100%" /><br />
      <b>Lead Banglish Reply</b>
    </td>
    <td align="center" width="50%">
      <img src="Urgent_Bangla_Reply.png" alt="Urgent Bangla Reply" width="100%" /><br />
      <b>Urgent Bangla Reply</b>
    </td>
  </tr>
  <tr>
    <td align="center" width="50%">
      <img src="Job_Bangla_Reply.png" alt="Job Bangla Reply" width="100%" /><br />
      <b>Job Bangla Reply</b>
    </td>
    <td align="center" width="50%">
      <img src="Spam_No_Reply.png" alt="Spam Reply" width="100%" /><br />
      <b>Spam Reply</b>
    </td>
  </tr>
</table>

## Where AI is used, and where it is not
| AI (language work) | Plain code (exact work) |
|---|---|
| classify the message (lead, faq, support, urgent, spam, job, minor) | Bangla digits, phone validation, finding numbers in text |
| extract name, company, role, programme, headcount | duplicate detection (same phone = same person) |
| write the reply in the sender's language | routing, priority, the 48-hour clock |
| write the weekly summary sentences | every number in the summary; checks the AI's numbers |
| second AI pass: fact-check each reply for invented claims | refuses any phone number that is not in the message |

## Run it
