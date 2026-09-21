1. All tables need all grid lines. Optionally alternating white/light gray row backgrounds. Table cells need more vertical padding. Be careful when table occur near the top of pages as the PDF has show double table header rows.
2. add a table of contents at the top, as well as author information.
3. when you describe scenarios/options using both charts and tables, always do it in this order. a) Short narrative bullets to explain the scenarios with names bolded, b) a table with scenarios as rows to show the details, c) any charts that have scenarios presented. 
4. Number the major sections and create clear, numbered subsection titles as well. The distinction between subsections was not clear. For example, there is subsection on Caching that starts right in the middle of paragraph. It should be its own sub or sub-sub section. and subsections need numbers and have their own lines, not start in the middle of a sentence or paragraph. Introduce the topic of each section or subsection with a sentence that a) explains what topic will present and b) why it's important. 
5. Add a Questions (with answers below each question) major section at the end of the report. 
   1. Q: would there be any value in introducing a soundex like filter in pgeo to assist when users don't know the spelling of items? pros/cons?
   2. Q: Is the SQL that powers pgeo operating as functions or store proceedures? why or why not? Any performance implications? flexibility challenges?
      1. Q: what is the process for preparing data updates on faster machines and then "installing" into the production servers? what are the pros/cons? Can a semi-automated process be created?
   3. What happens when queries/engines exceed the VM resources? Explain the failure modes? Can they be detected and warn the users? do the engines recover when query pressure backs off a little?
   4. Can pgeo be made to scale horizontally like pelias?
6. A Future Work (what's next) section, include explanation of the possible value of OpenResty or Omnigres, and say whether they would be worth testing. Also explain how much, how plausible, and the real-world value of completing each task would be.
7. in the forward-search pipeline graphic we need a little more room between the horizontal boxes
8. for all Figure and table captions, Bold both the figure/table number AND the short title that follows. The body of the caption remains normal. 
   1. is the data flow pipe diagram, create named vertical swim lanes for the colored boxes, then explain in more detail what is happening in each step named in the colored boxes. Create more diagrams to show more detail on the processing steps.
9. The load testing harness introduces a term "k6" what is that? make sure you introduce all new terms. Create a table of acronyms and terms in the early part of the report.
10. This is a scientific paper. All external facts need citations and references. Use numeric references tied to a bibliography at the end of the document.
11. Although the charts suggest it, we need to better describe apples-to-apples in terms of pgeo and pelias resource usage. Pelias using a ton of resources (for example memory) is always compared to pgeo using less. When we describe performance and scaling it always needs to be at as close as we can get to the same resource levels. And clearly 3 users is too low to be meaning as it doesn't push either engine enough. So maybe we need conduct other testing with the very lowest resources that pelias can use, but use those resources for full benchmarks for both engines. Key the 3-user target material, but add new tests with the pelias-floor resources.
12. The latency at 3-user target charts introduce scenarios such as api-p4, but they are not explained before the chart appears.
13. do the data volume impact testing for pgeo that was done for pelias and compare the two.
14. Move the features table to the beginning of the report. 
15. Remove pelias_maine project from the front of the report, as this is now mostly about pgeo (we might need a better name)
16. The report from 7:30 tonight is better, but the table formats are still rough. Try switching to generating .docx or Open Document docs first, then rendering PDFs from them. Though the font inside the tables of the .docx needs to be smaller for example. the wrapping in many tables make the entries hard to read. This report is built on linux, so the Open Document format is more native here than .docx
17. The new report introduces other postgres-only geocoders, but does not compare them against pgeo for features for example. Do that.  introduce pgeo features table and explanations first.
18.  Produce several short dummy reports in PDF with variations of fonts (both serif and sans), font sizes, and other formatting parameters (maybe as named themes) that I can pick from- again focusing scientific publication. The current PDF is ok, but we can do better.







