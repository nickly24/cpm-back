import importlib.util
import tempfile
import unittest
from pathlib import Path

import pikepdf

MODULE_PATH=Path(__file__).parents[1]/'cpm_back/services/homework_files/pdf_pipeline.py'
spec=importlib.util.spec_from_file_location('homework_pdf_pipeline',MODULE_PATH)
pipeline=importlib.util.module_from_spec(spec);spec.loader.exec_module(pipeline)


class PdfPipelineTest(unittest.TestCase):
    def test_valid_pdf_is_linearized_and_hashed(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source.pdf';output=Path(folder)/'output.pdf'
            pdf=pikepdf.Pdf.new();pdf.add_blank_page();pdf.save(source)
            result=pipeline.process_pdf(source,output,10*1024*1024,35)
            self.assertEqual(result['page_count'],1);self.assertEqual(len(result['sha256']),64)

    def test_36_pages_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source.pdf';output=Path(folder)/'output.pdf'
            pdf=pikepdf.Pdf.new()
            for _ in range(36):pdf.add_blank_page()
            pdf.save(source)
            with self.assertRaisesRegex(pipeline.PdfRejected,'too_many_pages'):
                pipeline.process_pdf(source,output,10*1024*1024,35)

    def test_35_pages_are_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source.pdf';output=Path(folder)/'output.pdf';pdf=pikepdf.Pdf.new()
            for _ in range(35):pdf.add_blank_page()
            pdf.save(source)
            self.assertEqual(pipeline.process_pdf(source,output,10*1024*1024,35)['page_count'],35)

    def test_input_size_boundary_and_plus_one(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source.pdf';output=Path(folder)/'output.pdf';pdf=pikepdf.Pdf.new();pdf.add_blank_page();pdf.save(source)
            limit=10*1024*1024
            with source.open('ab') as stream:stream.write(b' '*(limit-source.stat().st_size))
            self.assertEqual(source.stat().st_size,limit)
            pipeline.process_pdf(source,output,limit,35)
            with source.open('ab') as stream:stream.write(b' ')
            with self.assertRaisesRegex(pipeline.PdfRejected,'source_too_large'):
                pipeline.process_pdf(source,output,limit,35)

    def test_password_pdf_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source.pdf';output=Path(folder)/'output.pdf';pdf=pikepdf.Pdf.new();pdf.add_blank_page();pdf.save(source,encryption=pikepdf.Encryption(owner='owner',user='secret'))
            with self.assertRaisesRegex(pipeline.PdfRejected,'encrypted_pdf'):
                pipeline.process_pdf(source,output,10*1024*1024,35)

    def test_broken_pdf_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source.pdf';output=Path(folder)/'output.pdf';source.write_bytes(b'%PDF-broken')
            with self.assertRaisesRegex(pipeline.PdfRejected,'corrupt_pdf'):
                pipeline.process_pdf(source,output,10*1024*1024,35)

    def test_active_action_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source.pdf';output=Path(folder)/'output.pdf'
            pdf=pikepdf.Pdf.new();pdf.add_blank_page();pdf.Root['/OpenAction']=pikepdf.Dictionary(S=pikepdf.Name('/JavaScript'),JS='x');pdf.save(source)
            with self.assertRaisesRegex(pipeline.PdfRejected,'active_content'):
                pipeline.process_pdf(source,output,10*1024*1024,35)

    def test_spoofed_mime_signature_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source.pdf';output=Path(folder)/'output.pdf';source.write_bytes(b'not a pdf')
            with self.assertRaisesRegex(pipeline.PdfRejected,'invalid_pdf_signature'):
                pipeline.process_pdf(source,output,10*1024*1024,35)


if __name__=='__main__':unittest.main()
