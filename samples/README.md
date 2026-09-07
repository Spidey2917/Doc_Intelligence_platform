# samples/

Drop the sample corpus here. Everything in this folder except this README is
ignored by git, so real client documents cannot be committed by accident.

Layout the census expects (one sub-folder per document category, original
filenames kept, nothing cleaned or renamed):

    samples/
      vendor_payment_agreements/
      vendor_submission_forms/
      internal_claims/
      statements/
      unsorted/            # anything you cannot categorise yet

The folder name becomes the `category_hint` on every artifact that comes out
of that file, including attachments nested inside emails. Emails, zips and
nested emails are unpacked automatically, so leave containers as they are.

Run the census with:

    docintel census samples/
