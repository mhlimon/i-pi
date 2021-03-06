"""Contains the classes that deal with the different dynamics required in
different types of ensembles.

Holds the algorithms required for normal mode propagators, and the objects to
do the constant temperature and pressure algorithms. Also calculates the
appropriate conserved energy quantity for the ensemble of choice.
"""

# This file is part of i-PI.
# i-PI Copyright (C) 2014-2015 i-PI developers
# See the "licenses" directory for full license information.


import time

import numpy as np

from ipi.engine.motion import Motion
from ipi.utils.depend import *
from ipi.engine.thermostats import Thermostat
from ipi.engine.barostats import Barostat


#__all__ = ['Dynamics', 'NVEIntegrator', 'NVTIntegrator', 'NPTIntegrator', 'NSTIntegrator', 'SCIntegrator`']

class Dynamics(Motion):

    """self (path integral) molecular dynamics class.

    Gives the standard methods and attributes needed in all the
    dynamics classes.

    Attributes:
        beads: A beads object giving the atoms positions.
        cell: A cell object giving the system box.
        forces: A forces object giving the virial and the forces acting on
            each bead.
        prng: A random number generator object.
        nm: An object which does the normal modes transformation.

    Depend objects:
        econs: The conserved energy quantity appropriate to the given
            ensemble. Depends on the various energy terms which make it up,
            which are different depending on the ensemble.he
        temp: The system temperature.
        dt: The timestep for the algorithms.
        ntemp: The simulation temperature. Will be nbeads times higher than
            the system temperature as PIMD calculations are done at this
            effective classical temperature.
    """

    def __init__(self, timestep, mode="nve", thermostat=None, barostat=None, fixcom=False, fixatoms=None, nmts=None):
        """Initialises a "dynamics" motion object.

        Args:
            dt: The timestep of the simulation algorithms.
            fixcom: An optional boolean which decides whether the centre of mass
                motion will be constrained or not. Defaults to False.
        """

        super(Dynamics, self).__init__(fixcom=fixcom, fixatoms=fixatoms)
        dself = dd(self)

        dself.dt = depend_value(name='dt', value=timestep)
        if thermostat is None:
            self.thermostat = Thermostat()
        else:
            self.thermostat = thermostat

        if barostat is None:
            self.barostat = Barostat()
        else:
            self.barostat = barostat

        if nmts is np.zeros(0, int):
            self.nmts = np.asarray([1], int)
        elif nmts is None or len(nmts) == 0:
            self.nmts = np.asarray([1], int)
        else:
            self.nmts = np.asarray(nmts)

        self.enstype = mode
        if self.enstype == "nve":
            self.integrator = NVEIntegrator()
        elif self.enstype == "nvt":
            self.integrator = NVTIntegrator()
        elif self.enstype == "npt":
            self.integrator = NPTIntegrator()
        elif self.enstype == "nst":
            self.integrator = NSTIntegrator()
        elif self.enstype == "mts":
            self.integrator = MTSIntegrator()
        elif self.enstype == "sc":
            self.integrator = SCIntegrator()

        else:
            self.integrator = DummyIntegrator()

        self.fixcom = fixcom
        if fixatoms is None:
            self.fixatoms = np.zeros(0, int)
        else:
            self.fixatoms = fixatoms

    def bind(self, ens, beads, nm, cell, bforce, prng):
        """Binds ensemble beads, cell, bforce, and prng to the dynamics.

        This takes a beads object, a cell object, a forcefield object and a
        random number generator object and makes them members of the ensemble.
        It also then creates the objects that will hold the data needed in the
        ensemble algorithms and the dependency network. Note that the conserved
        quantity is defined in the init, but as each ensemble has a different
        conserved quantity the dependencies are defined in bind.

        Args:
            beads: The beads object from whcih the bead positions are taken.
            nm: A normal modes object used to do the normal modes transformation.
            cell: The cell object from which the system box is taken.
            bforce: The forcefield object from which the force and virial are
                taken.
            prng: The random number generator object which controls random number
                generation.
        """

        super(Dynamics, self).bind(ens, beads, nm, cell, bforce, prng)
        dself = dd(self)

        # Checks if the number of mts levels is equal to the dimensionality of the mts weights.
        if (len(self.nmts) != self.forces.nmtslevels):
            raise ValueError("The number of mts levels for the integrator does not agree with the mts_weights of the force components.")

        # Binds integrators
        self.integrator.bind(self)

        dself = dd(self)
        # n times the temperature (for path integral partition function)
        dself.ntemp = depend_value(name='ntemp', func=self.get_ntemp,
                                   dependencies=[dd(self.ensemble).temp])
        self.integrator.pconstraints()

        fixdof = len(self.fixatoms) * 3 * self.beads.nbeads
        if self.fixcom:
            fixdof += 3

        # first makes sure that the thermostat has the correct temperature, then proceed with binding it.
        dpipe(dself.ntemp, dd(self.thermostat).temp)
        dpipe(dself.dt, dd(self.thermostat).dt)

        # the free ring polymer propagator is called in the inner loop, so propagation time should be redefined accordingly.
        self.inmts = 1
        for mk in self.nmts: self.inmts *= mk
        dself.deltat = depend_value(name="deltat", func=(lambda: self.dt / self.inmts), dependencies=[dself.dt])
        dpipe(dself.deltat, dd(self.nm).dt)

        # depending on the kind, the thermostat might work in the normal mode or the bead representation.
        self.thermostat.bind(beads=self.beads, nm=self.nm, prng=prng, fixdof=fixdof)

        dbarostat = dd(self.barostat)
        dpipe(dself.ntemp, dbarostat.temp)
        dpipe(dself.dt, dbarostat.dt)
        dpipe(dd(self.ensemble).pext, dbarostat.pext)
        dpipe(dd(self.ensemble).stressext, dbarostat.stressext)

        #!TODO the barostat should also be connected to the bias stress
        self.barostat.bind(beads, nm, cell, bforce, prng=prng, fixdof=fixdof, bias=ens.bias)

        self.ensemble.add_econs(dd(self.thermostat).ethermo)
        self.ensemble.add_econs(dd(self.barostat).ebaro)

        # adds potential and kinetic energy for the barostat to the ensemble
        self.ensemble.add_xlpot(dd(self.barostat).pot)
        self.ensemble.add_xlkin(dd(self.barostat).kin)

        #!TODO THOROUGH CLEAN-UP AND CHECK
        # if self.enstype in ["nvt", "npt", "nst"]:
        if self.enstype == "nvt" or self.enstype == "npt" or self.enstype == "nst":
            if self.ensemble.temp < 0:
                raise ValueError("Negative or unspecified temperature for a constant-T integrator")
            if self.enstype == "npt":
                if type(self.barostat) is Barostat:
                    raise ValueError("The barostat and its mode have to be specified for constant-p integrators")
                if self.ensemble.pext < 0:
                    raise ValueError("Negative or unspecified pressure for a constant-p integrator")
            elif self.enstype == "nst":
                if np.trace(self.ensemble.stressext) < 0:
                    raise ValueError("Negative or unspecified stress for a constant-s integrator")

    def get_ntemp(self):
        """Returns the PI simulation temperature (P times the physical T)."""

        return self.ensemble.temp * self.beads.nbeads

    def step(self, step=None):
        self.integrator.step(step)


class DummyIntegrator(dobject):

    """ No-op integrator for (PI)MD """

    def __init__(self):
        pass

    def bind(self, motion):
        """ Reference all the variables for simpler access."""

        self.beads = motion.beads
        self.bias = motion.ensemble.bias
        self.ensemble = motion.ensemble
        self.forces = motion.forces
        self.prng = motion.prng
        self.nm = motion.nm
        self.thermostat = motion.thermostat
        self.barostat = motion.barostat
        self.fixcom = motion.fixcom
        self.fixatoms = motion.fixatoms
        dself = dd(self)
        dself.dt = dd(motion).dt
        if motion.enstype == "mts": self.nmts = motion.nmts
        # mts on sc force in suzuki-chin
        if motion.enstype == "sc":
            if(motion.nmts.size > 1):
                raise ValueError("MTS for SC is not implemented yet....")
            else:
                # coefficients to get the (baseline) trotter to sc conversion
                self.coeffsc = np.ones((self.beads.nbeads, 3 * self.beads.natoms), float)
                self.coeffsc[::2] /= -3.
                self.coeffsc[1::2] /= 3.
                self.nmts = motion.nmts[-1]

    def pstep(self):
        """Dummy momenta propagator which does nothing."""
        pass

    def qcstep(self):
        """Dummy centroid position propagator which does nothing."""
        pass

    def step(self, step=None):
        """Dummy simulation time step which does nothing."""
        pass

    def pconstraints(self):
        """Dummy centroid momentum step which does nothing."""
        pass


class NVEIntegrator(DummyIntegrator):

    """ Integrator object for constant energy simulations.

    Has the relevant conserved quantity and normal mode propagator for the
    constant energy ensemble. Note that a temperature of some kind must be
    defined so that the spring potential can be calculated.

    Attributes:
        ptime: The time taken in updating the velocities.
        qtime: The time taken in updating the positions.
        ttime: The time taken in applying the thermostat steps.

    Depend objects:
        econs: Conserved energy quantity. Depends on the bead kinetic and
            potential energy, and the spring potential energy.
    """

    def pconstraints(self):
        """This removes the centre of mass contribution to the kinetic energy.

        Calculates the centre of mass momenta, then removes the mass weighted
        contribution from each atom. If the ensemble defines a thermostat, then
        the contribution to the conserved quantity due to this subtraction is
        added to the thermostat heat energy, as it is assumed that the centre of
        mass motion is due to the thermostat.

        If there is a choice of thermostats, the thermostat
        connected to the centroid is chosen.
        """

        if (self.fixcom):
            pcom = np.zeros(3, float)

            na3 = self.beads.natoms * 3
            nb = self.beads.nbeads
            p = dstrip(self.beads.p)
            m = dstrip(self.beads.m3)[:, 0:na3:3]
            M = self.beads[0].M

            for i in range(3):
                pcom[i] = p[:, i:na3:3].sum()

            self.ensemble.eens += np.dot(pcom, pcom) / (2.0 * M * nb)

            # subtracts COM velocity
            pcom *= 1.0 / (nb * M)
            for i in range(3):
                self.beads.p[:, i:na3:3] -= m * pcom[i]

        if len(self.fixatoms) > 0:
            for bp in self.beads.p:
                m = dstrip(self.beads.m)
                self.ensemble.eens += 0.5 * np.dot(bp[self.fixatoms * 3], bp[self.fixatoms * 3] / m[self.fixatoms])
                self.ensemble.eens += 0.5 * np.dot(bp[self.fixatoms * 3 + 1], bp[self.fixatoms * 3 + 1] / m[self.fixatoms])
                self.ensemble.eens += 0.5 * np.dot(bp[self.fixatoms * 3 + 2], bp[self.fixatoms * 3 + 2] / m[self.fixatoms])
                bp[self.fixatoms * 3] = 0.0
                bp[self.fixatoms * 3 + 1] = 0.0
                bp[self.fixatoms * 3 + 2] = 0.0

    def pstep(self):
        """Velocity Verlet momenta propagator."""

        self.beads.p += dstrip(self.forces.f) * (self.dt * 0.5)
        # also adds the bias force
        self.beads.p += dstrip(self.bias.f) * (self.dt * 0.5)

    def qcstep(self):
        """Velocity Verlet centroid position propagator."""

        self.nm.qnm[0, :] += dstrip(self.nm.pnm)[0, :] / dstrip(self.beads.m3)[0] * self.dt

    def step(self, step=None):
        """Does one simulation time step."""

        self.ptime = -time.time()
        self.pstep()
        self.pconstraints()
        self.ptime += time.time()

        self.qtime = -time.time()
        self.qcstep()

        self.nm.free_qstep()
        self.qtime += time.time()

        self.ptime -= time.time()
        self.pstep()
        self.pconstraints()
        self.ptime += time.time()


class NVTIntegrator(NVEIntegrator):

    """Integrator object for constant temperature simulations.

    Has the relevant conserved quantity and normal mode propagator for the
    constant temperature ensemble. Contains a thermostat object containing the
    algorithms to keep the temperature constant.

    Attributes:
        thermostat: A thermostat object to keep the temperature constant.
    """

    def step(self, step=None):
        """Does one simulation time step."""

        self.ptime = 0
        self.ttime = 0
        self.qtime = 0

        self.ttime = -time.time()
        self.thermostat.step()
        self.pconstraints()
        self.ttime += time.time()

        self.ptime = -time.time()
        self.pstep()
        self.pconstraints()
        self.ptime += time.time()

        self.qtime = -time.time()
        self.qcstep()
        self.nm.free_qstep()
        self.qtime += time.time()

        self.ptime -= time.time()
        self.pstep()
        self.pconstraints()
        self.ptime += time.time()

        self.ttime -= time.time()
        self.thermostat.step()
        self.pconstraints()
        self.ttime += time.time()
        # print "PTIME: ", self.ptime, "  TTIME: ", self.ttime, "  QTIME: ", self.qtime


class NPTIntegrator(NVTIntegrator):

    """Integrator object for constant pressure simulations.

    Has the relevant conserved quantity and normal mode propagator for the
    constant pressure ensemble. Contains a thermostat object containing the
    algorithms to keep the temperature constant, and a barostat to keep the
    pressure constant.
    """

    def step(self, step=None):
        """NPT time step.

        Note that the barostat only propagates the centroid coordinates. If this
        approximation is made a centroid virial pressure and stress estimator can
        be defined, so this gives the best statistical convergence. This is
        allowed as the normal mode propagation is approximately unaffected
        by volume fluctuations as long as the system box is much larger than
        the radius of gyration of the ring polymers.
        """

        self.ttime = -time.time()
        self.thermostat.step()
        self.barostat.thermostat.step()
        self.pconstraints()
        self.ttime += time.time()

        self.ptime = -time.time()
        self.barostat.pstep()
        self.pconstraints()
        self.ptime += time.time()

        self.qtime = -time.time()
        self.barostat.qcstep()
        self.nm.free_qstep()
        self.qtime += time.time()

        self.ptime -= time.time()
        self.barostat.pstep()
        self.pconstraints()
        self.ptime += time.time()

        self.ttime -= time.time()
        self.thermostat.step()
        self.barostat.thermostat.step()
        self.pconstraints()
        self.ttime += time.time()


class NSTIntegrator(NVTIntegrator):

    """Ensemble object for constant pressure simulations.

    Has the relevant conserved quantity and normal mode propagator for the
    constant pressure ensemble. Contains a thermostat object containing the
    algorithms to keep the temperature constant, and a barostat to keep the
    pressure constant.

    Attributes:
    barostat: A barostat object to keep the pressure constant.

    Depend objects:
    econs: Conserved energy quantity. Depends on the bead and cell kinetic
    and potential energy, the spring potential energy, the heat
    transferred to the beads and cell thermostat, the temperature and
    the cell volume.
    pext: External pressure.
    """

    def step(self, step=None):
        """NST time step (dummy for now).

        Note that the barostat only propagates the centroid coordinates. If this
        approximation is made a centroid virial pressure and stress estimator can
        be defined, so this gives the best statistical convergence. This is
        allowed as the normal mode propagation is approximately unaffected
        by volume fluctuations as long as the system box is much larger than
        the radius of gyration of the ring polymers.
        """

        self.ttime = -time.time()
        self.thermostat.step()
        self.barostat.thermostat.step()
        self.pconstraints()
        self.ttime += time.time()

        self.ptime = -time.time()
        self.barostat.pstep()
        self.pconstraints()
        self.ptime += time.time()

        self.qtime = -time.time()
        self.barostat.qcstep()
        self.nm.free_qstep()
        self.qtime += time.time()

        self.ptime -= time.time()
        self.barostat.pstep()
        self.pconstraints()
        self.ptime += time.time()

        self.ttime -= time.time()
        self.barostat.thermostat.step()
        self.thermostat.step()
        self.pconstraints()
        self.ttime += time.time()


class SCIntegrator(NVEIntegrator):

    """Fourth order integrator object for constant temperature simulations.

    Has the relevant conserved quantity and normal mode propagator for the
    constant temperature ensemble. Contains a thermostat object containing the
    algorithms to keep the temperature constant.

    Attributes:
       thermostat: A thermostat object to keep the temperature constant.

    Depend objects:
       econs: Conserved energy quantity. Depends on the bead kinetic and
          potential energy, the spring potential energy and the heat
          transferred to the thermostat.
    """

    def bind(self, mover):
        """Binds ensemble beads, cell, bforce, bbias and prng to the dynamics.

        This takes a beads object, a cell object, a forcefield object and a
        random number generator object and makes them members of the ensemble.
        It also then creates the objects that will hold the data needed in the
        ensemble algorithms and the dependency network. Note that the conserved
        quantity is defined in the init, but as each ensemble has a different
        conserved quantity the dependencies are defined in bind.

        Args:
           beads: The beads object from whcih the bead positions are taken.
           nm: A normal modes object used to do the normal modes transformation.
           cell: The cell object from which the system box is taken.
           bforce: The forcefield object from which the force and virial are
              taken.
           prng: The random number generator object which controls random number
              generation.
        """

        super(SCIntegrator, self).bind(mover)
        self.ensemble.add_econs(dd(self.forces).potsc)
        self.ensemble.add_xlpot(dd(self.forces).potsc)

    def pstep(self):
        """Velocity Verlet momenta propagator."""

        # also include the baseline Tr2SC correction (the 2/3 & 4/3 V bit)
        self.beads.p += dstrip(self.forces.f) * (1 + self.coeffsc) * self.dt * 0.5 / self.nmts
        # also adds the bias force (TODO!!!)
        # self.beads.p += dstrip(self.bias.f)*(self.dt*0.5)

    def pscstep(self):
        """Velocity Verlet Suzuki-Chin momenta propagator."""

        # also adds the force assiciated with SuzukiChin correction (only the |f^2| term, so we remove the Tr2SC correction)
        self.beads.p += (dstrip(self.forces.fsc) - self.coeffsc * dstrip(self.forces.f)) * self.dt * 0.5

    def qcstep(self):
        """Velocity Verlet centroid position propagator."""

        self.nm.qnm[0, :] += dstrip(self.nm.pnm)[0, :] / dstrip(self.beads.m3)[0] * self.dt / self.nmts

    def step(self, step=None):
        """Does one simulation time step."""

        self.ttime = -time.time()
        self.thermostat.step()
        self.pconstraints()
        self.ttime += time.time()

        self.pscstep()

        for i in range(self.nmts):
            self.ptime = -time.time()
            self.pstep()
            self.pconstraints()
            self.ptime += time.time()

            self.qtime = -time.time()
            self.qcstep()
            self.nm.free_qstep()
            self.qtime += time.time()

            self.ptime -= time.time()
            self.pstep()
            self.ptime += time.time()

        self.pscstep()
        self.pconstraints()

        self.ttime -= time.time()
        self.thermostat.step()
        self.pconstraints()
        self.ttime += time.time()


class MTSIntegrator(NVEIntegrator):

    """Integrator object for constant temperature simulations.

    Has the relevant conserved quantity and normal mode propagator for the
    constant temperature ensemble. Contains a thermostat object containing the
    algorithms to keep the temperature constant.
    """

    def pstep(self, level=0, alpha=1.0):
        """Velocity Verlet monemtum propagator."""
        self.beads.p += self.forces.forces_mts(level) * 0.5 * (self.dt / alpha)

    def qcstep(self, alpha=1.0):
        """Velocity Verlet centroid position propagator."""
        self.nm.qnm[0, :] += dstrip(self.nm.pnm)[0, :] / dstrip(self.beads.m3)[0] * self.dt / alpha

    def mtsprop(self, index, alpha):
        """ Recursive MTS step """
        nmtslevels = len(self.nmts)
        mk = self.nmts[index]  # mtslevels starts at level zero, where nmts should be 1 in most cases
        alpha *= mk
        for i in range(mk):
            # propagate p for dt/2alpha with force at level index
            self.ptime = -time.time()
            self.pstep(index, alpha)
            self.pconstraints()
            self.ptime += time.time()

            if index == nmtslevels - 1:
                # call Q propagation for dt/alpha at the inner step
                self.qtime = -time.time()
                self.qcstep(alpha)
                self.nm.free_qstep()  # this has been hard-wired to use the appropriate time step with depend magic
                self.qtime += time.time()
            else:
                self.mtsprop(index + 1, alpha)

            # propagate p for dt/2alpha
            self.ptime = -time.time()
            self.pstep(index, alpha)
            self.pconstraints()
            self.ptime += time.time()

    def step(self, step=None):
        """Does one simulation time step."""

        # thermostat is applied at the outer loop
        self.ttime = -time.time()
        self.thermostat.step()
        self.pconstraints()
        self.ttime += time.time()

        # bias is applied at the outer loop too
        self.beads.p += dstrip(self.bias.f) * (self.dt * 0.5)

        self.mtsprop(0, 1.0)

        self.beads.p += dstrip(self.bias.f) * (self.dt * 0.5)

        self.ttime -= time.time()
        self.thermostat.step()
        self.pconstraints()
        self.ttime += time.time()
