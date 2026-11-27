using System;
using Grasshopper.Kernel;

namespace Otimiza.GH
{
    public class OtimizaInfo : GH_AssemblyInfo
    {
        public override string Name => "Otimiza";
        public override string Description =>
            "Otimização multiobjetivo: o Grasshopper gera, o motor em Python avalia na GPU (TCC).";
        public override Guid Id => new Guid("5b0f3c1e-2a47-4f8e-9d36-7c1a9e4b8d21");
        public override string AuthorName => "plugin-otimizacao";
        public override string Version => "0.1.0";
    }
}
