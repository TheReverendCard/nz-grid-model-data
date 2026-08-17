using Pkg
using JADE, JuMP, GLPK

workdir = get(ENV, "JADE_IMPULSE_WORKDIR", ".jade_impulse")
ENV["JADE_DIR"] = abspath(workdir)

optimizer = optimizer_with_attributes(GLPK.Optimizer, "msg_lev" => GLPK.GLP_MSG_OFF)

function run_case(inputdir::String, simdir::String)
    rundata = define_JADE_model(inputdir, run_file = "run")
    model = create_JADE_model(rundata, optimizer)

    # Put the EA-published policy where simulate() expects to find it.
    policy_src = joinpath(ENV["JADE_DIR"], "published_policy")
    policy_dst = joinpath(ENV["JADE_DIR"], "Output", rundata.data_dir, rundata.policy_dir)
    mkpath(policy_dst)
    cp(joinpath(policy_src, "cuts.json"), joinpath(policy_dst, "cuts.json"); force = true)
    cp(joinpath(policy_src, "rundata.json"), joinpath(policy_dst, "rundata.json"); force = true)

    sim = define_JADE_simulation(inputdir, run_file = "run")
    sim.sim_dir = simdir
    return simulate(model, sim)
end

println("Running baseline with EA published policy...")
run_case("baseline", "impulse_baseline")
println("Running +1 GWh pulse with the same EA published policy...")
run_case("pulse", "impulse_pulse")
println("JADE paired fixed-policy simulations complete")
