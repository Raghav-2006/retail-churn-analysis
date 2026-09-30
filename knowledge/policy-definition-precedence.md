---
id: policy-definition-precedence
title: Which definition wins when docs disagree
doc_type: policy
owner: Data Governance
last_updated: 2011-06-20
deprecated: false
---
# Definition precedence

When two documents define the same metric differently:

1. A doc marked **deprecated** never wins; follow its `superseded_by` doc.
2. A **metric_definition** doc owned by Finance, Customer Analytics or Operations beats FAQs,
   slide decks and team wikis (`doc_type: faq`).
3. Between two current docs of the same type, the one with the later `last_updated` wins.
4. A question that states its own definition ("count a customer as active if ...") overrides
   every doc.

Cite the doc you used. If no doc defines a term the question depends on, say so rather than
inventing a definition.
