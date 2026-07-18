import os
from pathlib import Path


class StorageNotConfigured(RuntimeError):
    pass


class HomeworkStorage:
    def __init__(self, config):
        self.config = config
        value = lambda name, default=None: config.get(name, default) if hasattr(config, 'get') else getattr(config, name, default)
        required = ('S3_BUCKET', 'S3_ACCESS_KEY_ID', 'S3_SECRET_ACCESS_KEY')
        if not all(value(name) for name in required):
            raise StorageNotConfigured('storage_not_configured')
        import boto3
        self.bucket = value('S3_BUCKET')
        self.client = boto3.client(
            's3', endpoint_url=value('S3_ENDPOINT_URL') or None,
            region_name=value('S3_REGION') or None,
            aws_access_key_id=value('S3_ACCESS_KEY_ID'),
            aws_secret_access_key=value('S3_SECRET_ACCESS_KEY'),
        )

    def upload_file(self, path, key, content_type='application/pdf'):
        self.client.upload_file(str(path), self.bucket, key, ExtraArgs={'ContentType': content_type})

    def download_file(self, key, path):
        self.client.download_file(self.bucket, key, str(path))

    def delete(self, key):
        if key:
            self.client.delete_object(Bucket=self.bucket, Key=key)

    def presign(self, key, filename, inline=True):
        disposition = 'inline' if inline else 'attachment'
        return self.client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': self.bucket, 'Key': key,
                'ResponseContentType': 'application/pdf',
                'ResponseContentDisposition': f'{disposition}; filename*=UTF-8\'\'{filename}',
            },
            ExpiresIn=(self.config.get('S3_PRESIGN_TTL_SECONDS', 300) if hasattr(self.config, 'get') else self.config.S3_PRESIGN_TTL_SECONDS),
        )

    def size_summary(self, prefix='processed/'):
        total = count = 0
        paginator = self.client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for item in page.get('Contents', []):
                count += 1
                total += int(item.get('Size', 0))
        return {'file_count': count, 'total_bytes': total}


def safe_pdf_filename(student_name, homework_name, submitted_at):
    def clean(value):
        return ''.join(c for c in str(value) if c not in '\\/:*?"<>|\r\n').strip() or 'Без названия'
    return f'{clean(student_name)} — {clean(homework_name)} — {submitted_at:%d.%m.%Y}.pdf'
