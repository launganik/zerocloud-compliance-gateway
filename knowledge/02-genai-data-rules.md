# GenAI data rules for bookings

> Demo knowledge base: summary of Compliance's input from 22 October 2026, to be confirmed by Data Protection. Not official Forto policy.

## What must not go to public cloud AI

Under Forto's GenAI guidelines, unredacted personal data, confidential contracts, HR and disciplinary files, and whistleblowing reports cannot be sent to public cloud AI tools.

## What may go to enterprise cloud AI

Generic product descriptions can often be handled by enterprise cloud AI that works under a data processing agreement which excludes training on our data. Sensitive cargo details benefit from local processing instead.

## Why the gateway runs on our own hardware

You only know whether a booking email contains sensitive material after reading it. The compliance gateway therefore reads every email on our own hardware first, with no external AI provider. Cleared bookings continue to ELM; held bookings stay on the box until a compliance manager decides.

## Accuracy requirement

For compliance decisions, the AI must link to official, deterministic control lists so restricted items are not missed. The AI only reads and flags; the lists and a human decide. When in doubt, the gateway holds.
