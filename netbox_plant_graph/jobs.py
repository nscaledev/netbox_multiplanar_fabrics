from netbox.jobs import JobRunner


class V2KernelHealthJob(JobRunner):
    class Meta:
        name = 'V2 Kernel Health'
        description = 'Return basic V2 multiplanar fabric plugin health.'

    def run(self, *args, **kwargs):
        from .models import Fabric, FabricArchitecture

        return {
            'status': 'v2_kernel',
            'architecture_count': FabricArchitecture.objects.count(),
            'fabric_count': Fabric.objects.count(),
        }
