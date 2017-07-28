import sys
import re
import os
import tempfile
import subprocess
import shutil
import multiprocessing
import logging
import csv

import Bio
import Bio.SeqIO
import Bio.SeqIO.QualityIO
import Bio.Alphabet.IUPAC
import Bio.Blast
import Bio.Blast.NCBIXML

import Bio.File
import Bio.SeqIO.FastaIO

import paftol.tools


logger = logging.getLogger(__name__)

keepTmp = False


def isSane(filename):
    """Check whether a file name is sane, in the sense that it does not contain any "funny" characters"""
    if filename == '':
        return False
    funnyCharRe = re.compile('[\t/ ;,$#]')
    m = funnyCharRe.search(filename)
    if m is not None:
        return False
    if filename[0] == '-':
        return False
    return True


def cmpExonerateResultByQueryAlignmentStart(e1, e2):
    """Comparator function for sorting C{ExonerateResult}s by query alignment start.

@param e1: first exonerate result
@type e1: C{ExonerateResult}
@param e2: second exonerate result
@type e2: C{ExonerateResult}
@return: one of -1, 0 or 1
@rtype: C{int}
"""
    if e1.queryAlignmentStart < e2.queryAlignmentStart:
        return -1
    elif e1.queryAlignmentStart > e2.queryAlignmentStart:
        return 1
    return 0


def generateFastqcDataFrame(fastqFname):
    """Method that runs fastqc and returns C{FastqcStats}.

@param fastqFname: fastq file name
@type fastFname: C{str}
@return: C{FastqcStats}
"""
    m = re.match('(.*)\\.fastq', fastqFname)
    if m is None:
        raise StandardError, 'failed to extract basename of fastq filename'
    fastqBasename = m.group(1)
    try: 
        tmpDirName = tempfile.mkdtemp()
        outFname = os.path.join(tmpDirName, '%s_fastqc' % fastqBasename, 'fastqc_data.txt')
        fastqcArgs = ['fastqc', '--extract', '--outdir', tmpDirName, '--nogroup', fastqFname]
        fastqcProcess = subprocess.check_call(fastqcArgs)
        fastqcStats = FastqcStats(outFname)
    finally:
        shutil.rmtree(tmpDirName)
    return fastqcStats


class FastqcDataFrame(paftol.tools.DataFrame):

    def __init__(self, columnHeaderList, description=None, result=None):
        super(FastqcDataFrame, self).__init__(columnHeaderList)
        self.description = description
        self.result = result
        self.annotations = {}


class FastqcStats(object):

    fastqcVersionRe = re.compile('##FastQC\t(.+)') 
    fastqcModuleStartRe = re.compile('>>([^\t]+)\t([^\t]+)')

    def readCompleteLine(self, f):
        l = f.readline()
        if len(l) == 0:
            raise StandardError, 'unexpected empty line'
        if l[-1] != '\n':
            raise StandardError, 'unexpected truncated line'
        return l

    def readTableHeader(self, f):
        # FIXME: should probably use readCompleteLine?
        l = f.readline()
        if l[0] != '#':
            raise StandardError, 'malformed FastQC table header: %s' % l.strip()
        return l[1:].strip().split('\t')

    def checkFastqcVersion(self, f):
        # FIXME: need to check for empty string (premature EOF) -- check all readline() uses for parsing
        l = self.readCompleteLine(f)
        m = self.fastqcVersionRe.match(l)
        if m is None:
            raise StandardError, 'malformed FastQC version line: %s' % l.strip()
        v = m.group(1)
        if v != '0.11.5':
            raise StandardError, 'unsupported FastQC version %s' % v

    def parseBasicStatistics(self, f):
        l = self.readCompleteLine(f)
        m = self.fastqcModuleStartRe.match(l)
        if m is None:
            raise StandardError, 'malformed FastQC module start (Basic Statistics): %s' % l.strip()
        description = m.group(1)
        result = m.group(2)
        if description != 'Basic Statistics':
            raise StandardError, 'expected "Basic Statistics" module but found "%s"' % description
        if self.readTableHeader(f) != ['Measure', 'Value']:
            raise StandardError, 'malformed "Basic Statistics" header: %s' % ', '.join(self.readTableHeader(f))
        fastqcDataFrame = FastqcDataFrame(['measure', 'value'], description, result)
        l = self.readCompleteLine(f)
        while l.strip() != '>>END_MODULE':
            w = l.strip().split('\t')
            if len(w) != 2:
                raise StandardError, 'malformed line: %s' % l.strip()
            fastqcDataFrame.addRow({'measure': w[0], 'value': w[1]})
            l = self.readCompleteLine(f)
        self.basicStatistics = fastqcDataFrame

    def parsePerBaseSequenceQuality(self, f):
        l = self.readCompleteLine(f)
        m = self.fastqcModuleStartRe.match(l)
        if m is None:
            raise StandardError, 'malformed FastQC module start (Per base sequence quality): %s' % l.strip()
        description = m.group(1)
        result = m.group(2)
        if description != 'Per base sequence quality':
            raise StandardError, 'expected "Per base sequency quality" module but found "%s"' % description
        if self.readTableHeader(f) != ['Base', 'Mean', 'Median', 'Lower Quartile', 'Upper Quartile', '10th Percentile', '90th Percentile']:
            raise StandardError, 'malformed "Per base sequence quality" header: %s' % ', '.join(self.readTableHeader(f))
        fastqcDataFrame = FastqcDataFrame(['base', 'mean', 'median', 'lowerQuartile', 'upperQuartile', 'percentile10', 'percentile90'], description, result)
        l = self.readCompleteLine(f)
        while l.strip() != '>>END_MODULE':
            w = l.strip().split('\t')
            if len(w) != 7:
                raise StandardError, 'malformed line: %s' % l.strip()
            fastqcDataFrame.addRow({'base': int(w[0]), 'mean': float(w[1]), 'median': float(w[2]), 'lowerQuartile': float(w[3]), 'upperQuartile': float(w[4]), 'percentile10': float(w[5]), 'percentile90': float(w[6])})
            l = self.readCompleteLine(f)
        self.perBaseSequenceQuality = fastqcDataFrame

    def parsePerTileSequenceQuality(self, f):
        l = self.readCompleteLine(f)
        m = self.fastqcModuleStartRe.match(l)
        if m is None:
            raise StandardError, 'malformed FastQC module start (Per tile sequence quality): %s' % l.strip()
        description = m.group(1)
        result = m.group(2)
        if description != 'Per tile sequence quality':
            raise StandardError, 'expected "Per tile sequence quality" module but found "%s"' % description
        if self.readTableHeader(f) != ['Tile', 'Base', 'Mean']:
            raise StandardError, 'malformed "Per tile sequence quality" header: %s' % ', '.join(self.readTableHeader(f))
        fastqcDataFrame = FastqcDataFrame(['tile', 'base', 'mean'], description, result)
        l = self.readCompleteLine(f)
        while l.strip() != '>>END_MODULE':
            w = l.strip().split('\t')
            if len(w) != 3:
                raise StandardError, 'malformed line: %s' % l.strip()
            fastqcDataFrame.addRow({'tile': int(w[0]), 'base': int(w[1]), 'mean': float(w[2])})
            l = self.readCompleteLine(f)
        self.perTileSequenceQuality = fastqcDataFrame

    def parsePerSequenceQualityScores(self, f):
        l = self.readCompleteLine(f)
        m = self.fastqcModuleStartRe.match(l)
        if m is None:
            raise StandardError, 'malformed FastQC module start (Per Sequence Quality Scores): %s' % l.strip()
        description = m.group(1)
        result = m.group(2)
        if description != 'Per sequence quality scores':
            raise StandardError, 'expected "Per sequence quality scores" module but found "%s"' % description
        if self.readTableHeader(f) != ['Quality', 'Count']:
            raise StandardError, 'malformed "Per sequence quality scores" header: %s' % ', '.join(self.readTableHeader(f))
        fastqcDataFrame = FastqcDataFrame(['quality', 'count'], description, result)
        l = self.readCompleteLine(f)
        while l.strip() != '>>END_MODULE':
            w = l.strip().split('\t')
            if len(w) != 2:
                raise StandardError, 'malformed line: %s' % l.strip()
            fastqcDataFrame.addRow({'quality': w[0], 'count': w[1]})
            l = self.readCompleteLine(f)
        self.perSequenceQualityScores = fastqcDataFrame

    def parsePerBaseSequenceContent(self, f):
        l = self.readCompleteLine(f)
        m = self.fastqcModuleStartRe.match(l)
        if m is None:
            raise StandardError, 'malformed FastQC module start (Per base sequence content): %s' % l.strip()
        description = m.group(1)
        result = m.group(2)
        if description != 'Per base sequence content':
            raise StandardError, 'expected "Per base sequence content" module but found "%s"' % description
        if self.readTableHeader(f) != ['Base', 'G', 'A', 'T', 'C']:
            raise StandardError, 'malformed "Per base sequence content" header: %s' % ', '.join(self.readTableHeader(f))
        fastqcDataFrame = FastqcDataFrame(['base', 'g', 'a', 't', 'c'], description, result)
        l = self.readCompleteLine(f)
        while l.strip() != '>>END_MODULE':
            w = l.strip().split('\t')
            if len(w) != 5:
                raise StandardError, 'malformed line: %s' % l.strip()
            fastqcDataFrame.addRow({'base': int(w[0]), 'g': float(w[1]), 'a': float(w[2]), 't': float(w[3]), 'c': float(w[4])})
            l = self.readCompleteLine(f)
        self.perBaseSequenceContent = fastqcDataFrame

    def parsePerSequenceGCContent(self, f):
        l = self.readCompleteLine(f)
        m = self.fastqcModuleStartRe.match(l)
        if m is None:
            raise StandardError, 'malformed FastQC module start (Per sequence GC content): %s' % l.strip()
        description = m.group(1)
        result = m.group(2)
        if description != 'Per sequence GC content':
            raise StandardError, 'expected "Per sequence GC content" module but found "%s"' % description
        if self.readTableHeader(f) != ['GC Content', 'Count']:
            raise StandardError, 'malformed "Per sequence GC content" header: %s' % ', '.join(self.readTableHeader(f))
        fastqcDataFrame = FastqcDataFrame(['gcContent', 'count'], description, result)
        l = self.readCompleteLine(f)
        while l.strip() != '>>END_MODULE':
            w = l.strip().split('\t')
            if len(w) != 2:
                raise StandardError, 'malformed line: %s' % l.strip()
            fastqcDataFrame.addRow({'gcContent': int(w[0]), 'count': float(w[0])})
            l = self.readCompleteLine(f)
        self.perSequenceGCContent = fastqcDataFrame

    def parsePerBaseNContent(self, f):
        l = self.readCompleteLine(f)
        m = self.fastqcModuleStartRe.match(l)
        if m is None:
            raise StandardError, 'malformed FastQC module start (Per base N content): %s' % l.strip()
        description = m.group(1)
        result = m.group(2)
        if description != 'Per base N content':
            raise StandardError, 'expected "Per base N content" module but found "%s"' % description
        if self.readTableHeader(f) != ['Base', 'N-Count']:
            raise StandardError, 'malformed "Per base N content" header: %s' % ', '.join(self.readTableHeader(f))
        fastqcDataFrame = FastqcDataFrame(['base', 'nCount'], description, result)
        l = self.readCompleteLine(f)
        while l.strip() != '>>END_MODULE':
            w = l.strip().split('\t')
            if len(w) != 2:
                raise StandardError, 'malformed line: %s' % l.strip()
            fastqcDataFrame.addRow({'base': int(w[0]), 'nCount': float(w[1])})
            l = self.readCompleteLine(f)
        self.perBaseNContent = fastqcDataFrame

    def parseSequenceLengthDistribution(self, f):
        sys.stderr.write('WARNING: FastQC module "Sequence length Distribution" not implemented\n')
        l = self.readCompleteLine(f)
        while l.strip() != '>>END_MODULE':
            l = self.readCompleteLine(f)
        self.sequenceLengthDistribution = None

    def parseSequenceDuplicationLevels(self, f):
        # fastqcDataFrame = FastqcDataFrame([ ... ], description, result)
        # fastqcDataFrame.annotaions['totalDeduplicatedPercentage'] = float( ... )
        sys.stderr.write('WARNING: FastQC module "Sequence Duplication Levels" not implemented\n')
        l = self.readCompleteLine(f)
        while l.strip() != '>>END_MODULE':
            l = self.readCompleteLine(f)
        self.sequenceDuplicationLevels = None

    def parseOverrepresentedSequences(self, f):
        sys.stderr.write('WARNING: FastQC module "Overrepresented sequences" not implemented\n')
        l = self.readCompleteLine(f)
        while l.strip() != '>>END_MODULE':
            l = self.readCompleteLine(f)
        self.overrepresentedSequences = None

    def parseAdapterContent(self, f):
        sys.stderr.write('WARNING: FastQC module "Adapter Content" not implemented\n')
        l = self.readCompleteLine(f)
        while l.strip() != '>>END_MODULE':
            l = self.readCompleteLine(f)
        self.adapterContent = None

    def parseKmerContent(self, f):
        sys.stderr.write('WARNING: FastQC module "Kmer Content" not implemented\n')
        l = self.readCompleteLine(f)
        while l.strip() != '>>END_MODULE':
            l = self.readCompleteLine(f)
        self.kmerContent = None

    def __init__(self, fastqcStatsFname):
        with open(fastqcStatsFname, 'r') as f:
            self.checkFastqcVersion(f)
            self.parseBasicStatistics(f)
            self.parsePerBaseSequenceQuality(f)
            self.parsePerTileSequenceQuality(f)
            self.parsePerSequenceQualityScores(f)
            self.parsePerBaseSequenceContent(f)
            self.parsePerSequenceGCContent(f)
            self.parsePerBaseNContent(f)
            self.parseSequenceLengthDistribution(f)
            self.parseSequenceDuplicationLevels(f)
            self.parseOverrepresentedSequences(f)
            self.parseAdapterContent(f)
            self.parseKmerContent(f)

    def getMedian(self, index):
        return float(self.perBaseSequenceQuality.getRowDict(index)['median'])

    def getN(self):
        l = []
        for index in range(len(self.perBaseNContent.rowDictList)):
            l.append(self.perBaseNContent.rowDictList[index]['nCount'])
        return l

    def calculateMeanStd(self, dataframe):
        colList = dataframe.columnHeaderList[:]
        colList.remove('base')
        params = {}
        for column in colList:
            l = []
            for row in range(len(dataframe.rowDictList)):
                l.append(float(dataframe.rowDictList[row][column]))
            params[column] = paftol.tools.MeanAndStddev(l)
        return params


class BwaParams(object):

    """Hold parameters for C{bwa} and provide argument vectors on that basis.

@ivar numThreads: BWA number of threads (C{-t} option)
@type numThreads: C{int}
@ivar minSeedLength: BWA minimum seed length (C{-k} option)
@type minSeedLength: C{int}
@ivar scoreThreshold: BWA score threshold for recording reads as mapped (C{-T} option)
@type scoreThreshold: C{int}
@ivar reseedTrigger: BWA re-seed trigger (C{-r} option)
@type reseedTrigger: C{float}
"""

    def __init__(self, numThreads=None, minSeedLength=None, scoreThreshold=None, reseedTrigger=None):
        self.numThreads = numThreads
        self.minSeedLength = minSeedLength
        self.scoreThreshold = scoreThreshold
        self.reseedTrigger = reseedTrigger

    def indexReferenceArgv(self, referenceFname):
        return ['bwa', 'index', referenceFname]

    def mappingMemArgv(self, referenceFname, forwardReadsFname, reverseReadsFname=None):
        argv = ['bwa', 'mem', '-M']
        if self.minSeedLength is not None:
            argv.extend(['-k', '%d' % self.minSeedLength])
        if self.reseedTrigger is not None:
            argv.extend(['-r', '%f' % self.reseedTrigger])
        if self.scoreThreshold is not None:
            argv.extend(['-T', '%d' % self.scoreThreshold])
        if self.numThreads is not None:
            argv.extend(['-t', '%d' % self.numThreads])
        argv.append(referenceFname)
        argv.append(forwardReadsFname)
        if reverseReadsFname is not None:
            argv.append(reverseReadsFname)
        return argv

    def referenceIndexArgv(self, referenceFname):
        return ['bwa', 'index', referenceFname]


class HybseqAnalyser(object):

    """Base class for Hybseq analysers.

Instances of this class take a FASTA file of target PAFTOL gene sequences
and FASTQ files (one or two, for single / paired end, respectively),
and provide methods for running analyses to reconstruct sequences of
the target genes.
"""

    def __init__(self, workdirTgz=None, workDirname='paftoolstmp'):
        # FIXME: temporary workdir management should be moved out of analyser -- perhaps into result?
        self.workdirTgz = workdirTgz
        self.workDirname = workDirname
        self.tmpDirname = None
        # parameters for ensuring file names don't clash, e.g. because paftolGene / organism name is same as targets basename etc.
        self.targetsFname = 'targets.fasta'
        self.geneFnamePattern = 'gene-%s.fasta'

    def analyse(self):
        raise StandardError('not implemented in this "abstract" base class')

    def setupTmpdir(self):
        if self.tmpDirname is not None:
            raise StandardError('illegal state: already have generated working directory %s' % self.tmpDirname)
        self.tmpDirname = tempfile.mkdtemp(prefix=self.workDirname)
        os.mkdir(self.makeWorkDirname())

    def cleanupTmpdir(self):
        if self.tmpDirname is not None:
            if keepTmp:
                logger.warning('not removing temporary directory %s', self.tmpDirname)
            else:
                shutil.rmtree(self.tmpDirname)
            self.tmpDirname = None

    def makeWorkDirname(self):
        if self.tmpDirname is None:
            raise StandardError('illegal state: no temporary directory and hence no working directory')
        # logger.debug('tmpDirname = %s, workDirname = %s', self.tmpDirname, self.workDirname)
        return os.path.join(self.tmpDirname, self.workDirname)

    def makeTargetsFname(self, absolutePath=False):
        if absolutePath:
            return os.path.join(self.makeWorkDirname(), self.targetsFname)
        else:
            return self.targetsFname

    def makeGeneFname(self, geneName, absolutePath=False):
        geneFname = self.geneFnamePattern % geneName
        if absolutePath:
            return os.path.join(self.makeWorkDirname(), geneFname)
        else:
            return geneFname

    def makeTgz(self):
        if self.workdirTgz is not None:
            if self.tmpDirname is None:
                raise StandardError('illegal state: no temporary directory generated')
            tmpTgz = os.path.join(self.tmpDirname, '%s.tgz' % self.workDirname)
            tgzArgv = ['tar', '-zcf', tmpTgz, self.workDirname]
            tgzProcess = subprocess.Popen(tgzArgv, cwd=self.tmpDirname)
            tgzReturncode = tgzProcess.wait()
            if tgzReturncode != 0:
                raise StandardError('process "%s" returned %d' % (' '.join(tgzArgv), tgzReturncode))
            # FIXME: clumsy to first create tgz in temp dir and then
            # moving it to final destination, compute absolute path to
            # final destination and use that directly?
            shutil.move(os.path.join(self.tmpDirname, tmpTgz), self.workdirTgz)


class SamAlignment(object):

    """Class to represent a SAM record.
This class follows the naming and definitions of the SAMv1 spec. It is incomplete
to provide fields required for Hyb-Seq analysis only.

@ivar qname: SAM query name (C{QNAME}), read or read pair ID
@type qname: C{str}
@ivar rname: SAM reference name (C{RNAME})
@type rname: C{str}
@ivar flag: SAM flag (C{FLAG})
@type flag: C{int}
@ivar pos: SAM mapping position (C{POS})
@type pos: C{int}
@ivar mapq: SAM mapping quality (C{MAPQ})
@type mapq: C{int}
@ivar cigar: SAM CIGAR string (unexpanded) (C{CIGAR})
@type mapq: C{str}
@ivar seq: SAM query (read) sequence (C{SEQ})
@type seq: C{str}
"""

    cigarElementRe = re.compile('([0-9]+)([MIDNSHP=X])')

    def __init__(self, samLine):
        if samLine[-1] == '\n':
            samLine = samLine[:-1]
        w = samLine.split('\t')
        self.qname = w[0]
        self.flag = int(w[1])
        self.rname = w[2]
        self.pos = int(w[3])
        self.mapq = int(w[4])
        self.cigar = w[5]
        self.seq = w[9]

    def isMapped(self):
        return self.flag & 4 == 0

    def getMatchLength(self):
        e = self.expandedCigar()
        return e.count('M') + e.count('D')

    def getEndpos(self):
        return self.pos + self.getMatchLength()

    def expandedCigar(self):
        if self.cigar is None:
            return None
        e = ''
        c = self.cigar
        while c != '':
            m = self.cigarElementRe.match(c)
            if m is None:
                raise StandardError('malformed CIGAR "%s" (stuck at "%s")' % (self.cigar, c))
            e = e + (m.group(2) * int(m.group(1)))
            c = c[len(m.group()):]
        return e

    def numCigarMatches(self):
        e = self.expandedCigar()
        if e is None:
            return None
        if e.count('=') > 0:
            logger.warning('found sequence match ("=") characters, unimplemented')
        if e.count('X') > 0:
            logger.warning('found sequence mismatch ("X") characters, unimplemented')
        return e.count('M')


class MappedRead(object):
    """Represent a mapping of an NGS read to a PaftolTarget.
"""

    def __init__(self, paftolTarget):
        self.paftolTarget = paftolTarget

    def getReadName(self):
        raise StandardError, 'abstract method not overridden'

    def getMappingScore(self):
        raise StandardError, 'abstract method not overridden'
        

class SamMappedRead(MappedRead):

    def __init__(self, paftolTarget, samAlignment):
        super(SamMappedRead, self).__init__(paftolTarget)
	self.samAlignment = samAlignment

    def getReadName(self):
        return self.samAlignment.qname

    def getMappingScore(self):
        return self.samAlignment.mapq



class BlastMappedRead(MappedRead):
    """Represent a mapping of a read to a PaftolTarget based on BLAST.

The mapping score is based on the best HSP only, other HSPs are not
taken into account.
"""

    def __init__(self, paftolTarget, blastAlignment):
        super(BlastMappedRead, self).__init__(paftolTarget)
	self.blastAlignment = blastAlignment

    def getReadName(self):
        return self.blastAlignment.hit_id

    def getMappingScore(self):
        return self.blastAlignment.hsps[0].score


class PaftolTarget(object):

    """Represent a PAFTOL target, specific to an organism (i.e. species, specimen etc.).

The main content of instances of this class is a C{SeqRecord}
containing the sequence of the gene in the organism, thus
facilitating handling of multiple genes and multiple organisms.

@ivar organism: the organism
@type organism: C{Organism}
@ivar paftolGene: the PAFTOL gene
@type paftolGene: C{PaftolGene}
@ivar seqRecord: the sequence of this gene in this organism
@type seqRecord: C{Bio.SeqRecord.SeqRecord}
"""

    csvFieldNames = ['organism', 'gene', 'seqLength', 'numMappedReads']

    def __init__(self, organism, paftolGene, seqRecord):
        self.organism = organism
        self.paftolGene = paftolGene
        self.seqRecord = seqRecord
        self.mappedReadList = []
	# self.readAssociationList = []
        if paftolGene.name in organism.paftolTargetDict or organism.name in paftolGene.paftolTargetDict:
            raise StandardError('duplicate organism/gene: organism = %s, gene = %s, seqId = %s' % (organism.name, paftolGene.name, seqRecord.id))
        organism.paftolTargetDict[paftolGene.name] = self
        paftolGene.paftolTargetDict[organism.name] = self

    def addMappedRead(self, mappedRead):
        self.mappedReadList.append(mappedRead)

    def mappingScoreSum(self):
        if len(self.mappedReadList) == 0:
            return None
        return sum([mr.getMappingScore() for mr in self.mappedReadList])

    def getReadNameSet(self):
        # FIXME: may have to trim away "/1", "/2"?
        return set([mr.getReadName() for mr in self.mappedReadList])

    def numMappedReads(self):
        return len(self.mappedReadList)

    def csvRowDict(self):
        d = {}
        d['organism'] = self.organism.name
        d['gene'] = self.paftolGene.name
        d['seqLength'] = len(self.seqRecord)
        d['numMappedReads'] = self.numMappedReads()
        return d


class Organism(object):

    """Represent an organism (in the GenBank / NCBI sense of the term).

@ivar name: this organism's name
@type name: C{str}
@ivar paftolTargetDict: dictionary of genes in this organism
@type paftolTargetDict: C{dict} of C{PaftolTarget} instances with PAFTOL gene names as keys
"""

    csvFieldNames = ['organism', 'numGenes', 'numMappedReads']

    def __init__(self, name):
        self.name = name
        self.paftolTargetDict = {}

    def numMappedReads(self):
        return sum([t.numMappedReads() for t in self.paftolTargetDict.values()])

    def csvRowDict(self):
        d = {}
        d['organism'] = self.name
        d['numGenes'] = len(self.paftolTargetDict)
        d['numMappedReads'] = self.numMappedReads()
        return d


class PaftolGene(object):

    """Represent a PAFTOL gene.

This class does not represent genes in terms of intron / exon models
and other features. Its main purpose is to contain a collection of
PAFTOL targets, i.e. sequences found for this gene in various
organisms.

@ivar name: the name of this PAFTOL gene
@type name: C{str}
@ivar paftolTargetDict: dictionary of organisms with this PAFTOL gene
@type paftolTargetDict: C{dict} of C{PaftolTarget} instances with organism names as keys
"""

    csvFieldNames = ['gene', 'numOrganisms', 'meanSeqLength', 'numMappedReads']

    def __init__(self, name):
        self.name = name
        self.paftolTargetDict = {}

    def getReadNameSet(self):
        s = set()
        for paftolTarget in self.paftolTargetDict.values():
            s = s | paftolTarget.getReadNameSet()
        return s

    def meanSequenceLength(self):
        if len(self.paftolTargetDict) == 0:
            return None
        else:
            return float(sum([len(t.seqRecord) for t in self.paftolTargetDict.values()])) / float(len(self.paftolTargetDict))

    def numMappedReads(self):
        return sum([t.numMappedReads() for t in self.paftolTargetDict.values()])

    def csvRowDict(self):
        d = {}
        d['gene'] = self.name
        d['numOrganisms'] = len(self.paftolTargetDict)
        d['meanSeqLength'] = self.meanSequenceLength()
        d['numMappedReads'] = self.numMappedReads()
        return d


class PaftolTargetSet(object):

    paftolTargetRe = re.compile('([^-]+)-([^-]+)')

    def __init__(self):
        self.paftolGeneDict = {}
        self.organismDict = {}
        self.numOfftargetReads = 0
        self.fastaHandleStr = None

    def makeFastaId(organismName, geneName):
        return '%s-%s' % (organismName, geneName)

    def extractOrganismAndGeneNames(self, s):
        m = self.paftolTargetRe.match(s)
        if m is not None:
            organismName = m.group(1)
            geneName = m.group(2)
        else:
            organismName = 'unknown'
            geneName = s
        return organismName, geneName

    def readFasta(self, fastaHandle):
        # FIXME: add provision to control tolerance for invalid bases -- checkTargets type functionality?
        self.paftolGeneDict = {}
        self.organismDict = {}
        self.fastaHandleStr = str(fastaHandle)
        for sr in Bio.SeqIO.parse(fastaHandle, 'fasta', alphabet=Bio.Alphabet.IUPAC.ambiguous_dna):
            organismName, geneName = self.extractOrganismAndGeneNames(sr.id)
            if not isSane(organismName):
                raise StandardError('bad organism name: %s' % organismName)
            if not isSane(geneName):
                raise StandardError('bad gene name: %s' % geneName)
            if organismName not in self.organismDict:
                self.organismDict[organismName] = Organism(organismName)
            if geneName not in self.paftolGeneDict:
                self.paftolGeneDict[geneName] = PaftolGene(geneName)
            paftolTarget = PaftolTarget(self.organismDict[organismName], self.paftolGeneDict[geneName], sr)

    def meanTargetLength(self, geneName):
        if geneName not in self.paftolGeneDict:
            raise StandardError, 'gene %s not contained in this target set'
        return self.paftolGeneDict[geneName].meanSequenceLength()

    def getSeqRecordList(self):
        srList = []
        for organism in self.organismDict.values():
            for paftolTarget in organism.paftolTargetDict.values():
                srList.append(paftolTarget.seqRecord)
        return srList

    def writeFasta(self, fastaHandle):
        srList = self.getSeqRecordList()
        sys.stderr.write('writeFasta: writing %d sequences\n' % len(srList))
        Bio.SeqIO.write(srList, fastaHandle, 'fasta')

    def processSamAlignment(self, samAlignment):
        if samAlignment.isMapped():
            organismName, geneName = self.extractOrganismAndGeneNames(samAlignment.rname)
            if organismName not in self.organismDict:
                raise StandardError('unknown organism: %s' % organismName)
            if geneName not in self.paftolGeneDict:
                raise StandardError('unknown gene: %s' % geneName)
            if geneName not in self.organismDict[organismName].paftolTargetDict:
                raise StandardError('no entry for gene %s in organism %s' % (geneName, organismName))
            paftolTarget = self.organismDict[organismName].paftolTargetDict[geneName]
	    mappedRead = SamMappedRead(paftolTarget, samAlignment)

	    paftolTarget.addMappedRead(mappedRead)
        else:
            self.numOfftargetReads = self.numOfftargetReads + 1
            
    def makeReadNameGeneDict(self):
        readNameGeneDict = {}
        for paftolGene in self.paftolGeneDict.values():
            for readName in paftolGene.getReadNameSet():
                if readName not in readNameGeneDict:
                    readNameGeneDict[readName] = []
                readNameGeneDict[readName].append(paftolGene)
        return readNameGeneDict

    def targetStats(self):
        dataFrame = paftol.tools.DataFrame(PaftolTarget.csvFieldNames)
        for organism in self.organismDict.values():
            for paftolTarget in organism.paftolTargetDict.values():
                dataFrame.addRow(paftolTarget.csvRowDict())
        return dataFrame

    def geneStats(self):
        dataFrame = paftol.tools.DataFrame(PaftolGene.csvFieldNames)
        for paftolGene in self.paftolGeneDict.values():
            dataFrame.addRow(paftolGene.csvRowDict())
        return dataFrame

    def organismStats(self):
        dataFrame = paftol.tools.DataFrame(Organism.csvFieldNames)
        for organism in self.organismDict.values():
            dataFrame.addRow(organism.csvRowDict())
        return dataFrame
        

    def numMappedReads(self):
        n = 0
        for organism in self.organismDict.values():
            for paftolTarget in organism.paftolTargetDict.values():
                n = n + paftolTarget.numMappedReads()
        return n

    # FIXME: untested after transplant from HybpiperAnalyser
    def sanityCheck(self, allowInvalidBases=False):
        for organism in self.organismDict.values():
            for paftolTarget in organism.paftolTargetDict.values():
		if not allowInvalidBases:
		    setDiff = set(str(paftolTarget.seqRecord.seq).lower()) - set('acgt')
		    if len(setDiff) != 0:
			raise StandardError('target %s: illegal base(s) %s' % (paftolTarget.seqRecord.id, ', '.join(setDiff)))


class ReferenceGene(object):

    def __init__(self, geneId, referenceGenome, seqRecord, geneFeature, mrnaFeatureList=None, cdsFeatureList=None):
        self.geneId = geneId
        self.referenceGenome = referenceGenome
        self.seqRecord = seqRecord
        self.geneFeature = geneFeature
        self.mrnaFeatureList = [] if mrnaFeatureList is None else mrnaFeatureList[:]
        self.cdsFeatureList = [] if cdsFeatureList is None else cdsFeatureList[:]

    def getSequenceId(self):
        return self.seqRecord.id.split('.')[0]

    def containsHsp(self, hspAccession, hsp):
        if self.getSequenceId() != hspAccession:
            return False
        return self.geneFeature.location.start <= hsp.sbjct_start and self.geneFeature.location.end >= hsp.sbjct_end

    def getLength(self):
        return abs(self.geneFeature.location.end - self.geneFeature.location.start)

    def containsSamAlignment(self, samAlignment):
        if self.getSequenceId() != samAlignment.rname:
            return False
        return self.geneFeature.location.start <= samAlignment.pos and self.geneFeature.location.end >= samAlignment.getEndpos()

    def getGeneName(self):
        if 'name' in self.geneFeature.qualifiers:
            return self.geneFeature.qualifiers['name'][0]
        else:
            return None

    def getGeneNote(self):
        if 'note' in self.geneFeature.qualifiers:
            return self.geneFeature.qualifiers['note'][0]
        else:
            return None

    def getMrnaProduct(self):
        # CHECKME: returning 'product' qualifier value from feature with that qualifier -- may be more thorough to check that all are the same?
        for mrnaFeature in self.mrnaFeatureList:
            if 'product' in mrnaFeature.qualifiers:
                return mrnaFeature.qualifiers['product'][0]
        return None

    def getCdsProduct(self):
        # CHECKME: returning 'product' qualifier value from feature with that qualifier -- may be more thorough to check that all are the same?
        for cdsFeature in self.cdsFeatureList:
            if 'product' in cdsFeature.qualifiers:
                return cdsFeature.qualifiers['product'][0]
        return None


class ReferenceGenomeMappingProcessor(object):

    def __init__(self, referenceGenome):
        if referenceGenome.genomeLength is None:
            raise StandardError, 'reference genome length is None'
        self.referenceGenome = referenceGenome
        self.intergenicId = 'intergenic'
        self.unmappedId = 'unmapped'
        self.geneHitDict = {}
        self.intergenicLength = referenceGenome.genomeLength
        for gene in referenceGenome.geneList:
            geneLength = gene.getLength()
            self.geneHitDict[gene.geneId] = {'geneId': gene.geneId, 'geneLength': geneLength, 'numHits': 0}
            self.intergenicLength = self.intergenicLength - geneLength
        self.geneHitDict[self.intergenicId] = {'geneId': self.intergenicId, 'geneLength': self.intergenicLength, 'numHits': 0}
        self.geneHitDict[self.unmappedId] = {'geneId': self.unmappedId, 'geneLength': None, 'numHits': 0}
        self.rawmapTable = paftol.tools.DataFrame(['qname', 'rname', 'pos'])

    def getStatsTable(self):
        statsTable = paftol.tools.DataFrame(['geneId', 'geneLength', 'numHits'])
        for gene in self.referenceGenome.geneList:
            statsTable.addRow(self.geneHitDict[gene.geneId])
        statsTable.addRow(self.geneHitDict[self.intergenicId])
        statsTable.addRow(self.geneHitDict[self.unmappedId])
        return statsTable

    def processSamAlignment(self, samAlignment):
        if samAlignment.isMapped():
            self.rawmapTable.addRow({'qname': samAlignment.qname, 'rname': samAlignment.rname, 'pos': samAlignment.pos})
            geneId = self.referenceGenome.findGeneIdForSamAlignment(samAlignment)
            if geneId is None:
                geneId = self.intergenicId
        else:
            geneId = self.unmappedId
        self.geneHitDict[geneId]['numHits'] = self.geneHitDict[geneId]['numHits'] + 1


class ReferenceGenome(object):

    """Represent a reference genome, provided via FASTA and GenBank files (possibly both).

@ivar name: the name of this reference genome
@type name: C{str}
@ivar fastaFname: name of FASTA file containing the sequences of this genome
@type fastaFname: C{str}
@ivar genbankFname: name of GenBank file containing the sequences of this genome
@type genbankFname: C{str}
"""

    def __init__(self, name, fastaFname, genbankFname):
        self.name = name
        self.fastaFname = fastaFname
        self.genbankFname = genbankFname
        self.geneList = None
        self.genomeLength = None

    def scanGenesAth(self):
        if self.genbankFname is None:
            raise StandardError('no GenBank file name, cannot scan genes (ath method)')
        mrnaFeatureDict = {}
        cdsFeatureDict = {}
        self.geneList = []
        self.genomeLength = 0
        geneDict = {}
        with open(self.genbankFname, 'r') as f:
            for seqRecord in Bio.SeqIO.parse(f, 'genbank'):
                self.genomeLength = self.genomeLength + len(seqRecord)
                for seqFeature in seqRecord.features:
                    if seqFeature.type == 'gene':
                        # CHECKME: just presuming that locus_tag qualifier will always be present and have exactly one value
                        geneId = seqFeature.qualifiers['locus_tag'][0]
                        if geneId in geneDict:
                            raise StandardError('duplicate gene id: %s' % geneId)
                        gene = ReferenceGene(geneId, self, seqRecord, seqFeature)
                        self.geneList.append(gene)
                        geneDict[geneId] = gene
        # somewhat clumsy to re-scan GenBank file for additional features...
        with open(self.genbankFname, 'r') as f:
            for seqRecord in Bio.SeqIO.parse(f, 'genbank'):
                for seqFeature in seqRecord.features:
                    if seqFeature.type == 'mRNA':
                        geneId = seqFeature.qualifiers['locus_tag'][0]
                        if geneId in geneDict:
                            geneDict[geneId].mrnaFeatureList.append(seqFeature)
                    elif seqFeature.type == 'CDS':
                        geneId = seqFeature.qualifiers['locus_tag'][0]
                        if geneId in geneDict:
                            geneDict[geneId].cdsFeatureList.append(seqFeature)

    def scanGenes(self, scanMethod):
        """Populate C{self.geneList} by scanning an appropriate file.

Currently, the only method supported is C{ath}, which is designed to
work with the Arabidopsis thaliana genome (specifically the TAIR10
release). Currently, specifying any other method will raise an
exception. In the future, more genomes with different annotation
conventions may be added.

@param scanMethod: the method to use for scanning genes
@type scanMethod: C{str}
        """
        if scanMethod == 'ath':
            self.scanGenesAth()
        else:
            raise StandardError('unknown gene scan method: %s' % scanMethod)

    def findGenesByHsp(self, hspAccession, hsp):
        """Find genes that contain a given HSP.
"""
        geneList = []
        for gene in self.geneList:
            if gene.containsHsp(hspAccession, hsp):
                geneList.append(gene)
        return geneList

    def blastTargetSet(self, paftolTargetSet):
        blastnArgv = ['blastn', '-db', self.fastaFname, '-outfmt', '5']
        # blastnArgv = ['tee', 'tee.txt']
        logger.debug('%s', ' '.join(blastnArgv))
        # sys.stderr.flush()
        # sys.stdout.flush()
        blastnProcess = subprocess.Popen(blastnArgv, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        # subprocess.call(['lsof', '-p', '%d' % os.getpid()])
        # blastnProcess.stdin.flush()
        pid = os.fork()
        if pid == 0:
            # reload(Bio.SeqIO)
            blastnProcess.stdout.close()
            # paftolTargetSet.writeFasta(sys.stderr)
            # srList = paftolTargetSet.getSeqRecordList()
            # sys.stderr.write('target set has %d seqRecords\n' % len(srList))
            # sr = Bio.SeqRecord.SeqRecord(Bio.Seq.Seq('A'), id = 'srDummy', description = '')
            # sq = Bio.Seq.Seq('A')
            # s = str(sr.seq)
            # Bio.SeqIO.write([sr], sys.stderr, 'fasta')
            # sys.stderr.write(sr.format('fasta'))
            # for i in xrange(333):
            #     sys.stderr.write('>dummy\n')
            #     for j in xrange(10):
            #         sys.stderr.write('%s\n' % ('A' * 60))
            # with Bio.File.as_handle(sys.stderr) as h:
                # sys.stderr.write('biopython version: %s\n' % Bio.__version__)
                # sys.stderr.write('handle of sys.stderr: %s\n' % str(h))
                # w = Bio.SeqIO.FastaIO.FastaWriter(h)
                # sys.stderr.write('writer: %s\n' % str(w))
                # w.write_file([sr])
                # w.write_header()
                # w.write_records([sr])
                # w.handle.write('>someseq\n')
                # s = str(sr.seq)
                # w.handle.write(str(sr.seq) + '\n')
                # w.handle.write(sr.format('fasta'))
            # x = sr.format('fasta')
            # paftolTargetSet.writeFasta(blastnProcess.stdin)
            # FIXME: generating FASTA string and writing that manually to work around unresolved broken pipe issue
            for sr in paftolTargetSet.getSeqRecordList():
                blastnProcess.stdin.write(sr.format('fasta'))
            blastnProcess.stdin.close()
            os._exit(0)
        blastnProcess.stdin.close()
        # dict solely serves to check for duplicate BLAST records
        targetIdToGeneDict = {}
        targetGeneTable = paftol.tools.DataFrame(['targetId', 'geneId', 'geneName', 'geneNote', 'mrnaProduct', 'cdsProduct'])
        for blastRecord in Bio.Blast.NCBIXML.parse(blastnProcess.stdout):
            targetId = blastRecord.query
            if targetId in targetIdToGeneDict:
                raise StandardError('duplicate BLAST record for target %s' % targetId)
            geneList = []
            for blastAlignment in blastRecord.alignments:
                for hsp in blastAlignment.hsps:
                    for gene in self.findGenesByHsp(blastAlignment.accession, hsp):
                        if gene not in geneList:
                            dfRow = {}
                            dfRow['targetId'] = targetId
                            dfRow['geneId'] = gene.geneId
                            dfRow['geneName'] = gene.getGeneName()
                            dfRow['geneNote'] = gene.getGeneNote()
                            dfRow['mrnaProduct'] = gene.getMrnaProduct()
                            dfRow['cdsProduct'] = gene.getCdsProduct()
                            targetGeneTable.addRow(dfRow)
                            geneList.append(gene)
            targetIdToGeneDict[targetId] = geneList
        blastnProcess.stdout.close()
        wPid, wExit = os.waitpid(pid, 0)
        if pid != wPid:
            raise StandardError('wait returned pid %s (expected %d)' % (wPid, pid))
        if wExit != 0:
            raise StandardError('wait on forked process returned %d' % wExit)
        blastnReturncode = blastnProcess.wait()
        if blastnReturncode != 0:
            raise StandardError('blastn process exited with %d' % blastnReturncode)
        return targetGeneTable

    def findGeneIdForSamAlignment(self, samAlignment):
        # FIXME: clumsy linear search
        for gene in self.geneList:
            if gene.containsSamAlignment(samAlignment):
                return gene.geneId

    def mapReadsStatsBwaMem(self, bwaRunner, forwardReadsFname, reverseReadsFname=None):
        referenceGenomeMappingProcessor = ReferenceGenomeMappingProcessor(self)
        # continue here
        bwaRunner.processBwa(referenceGenomeMappingProcessor, forwardReadsFname, reverseReadsFname)
        return referenceGenomeMappingProcessor.getStatsTable(), referenceGenomeMappingProcessor.rawmapTable
    
    
class HybpiperAnalyser(HybseqAnalyser):
    
    def __init__(self, workdirTgz, workDirname):
        super(HybpiperAnalyser, self).__init__(workdirTgz, workDirname)

    def cleanup(self):
        self.cleanupTmpdir()

    def setRepresentativeGenes(self, result):
        """Roughly equivalent to "distribute targets" in HybPiper."""
        result.representativePaftolTargetDict = {}
        for geneName in result.paftolTargetSet.paftolGeneDict:
            representativePaftolTarget = None
            maxMappingScoreSum = None
            for organismName in result.paftolTargetSet.paftolGeneDict[geneName].paftolTargetDict:
                mappingScoreSum = result.paftolTargetSet.paftolGeneDict[geneName].paftolTargetDict[organismName].mappingScoreSum()
                if representativePaftolTarget is None or (mappingScoreSum is not None and mappingScoreSum > maxMappingScoreSum):
                    representativePaftolTarget = result.paftolTargetSet.paftolGeneDict[geneName].paftolTargetDict[organismName]
                    maxMappingScoreSum = mappingScoreSum
            result.representativePaftolTargetDict[geneName] = representativePaftolTarget
            if representativePaftolTarget is None:
                logger.debug('represenative for %s: none', geneName)
            else:
                logger.debug('representative for %s: %s', representativePaftolTarget.paftolGene.name, representativePaftolTarget.organism.name)

    def distributeSingle(self, result):
        fForward = open(result.forwardFastq, 'r')
        fqiForward = Bio.SeqIO.QualityIO.FastqGeneralIterator(fForward)
        readNameGeneDict = result.paftolTargetSet.makeReadNameGeneDict()
        for fwdReadTitle, fwdReadSeq, fwdReadQual in fqiForward:
            readName = fwdReadTitle.split()[0]
            if readName in readGenedict:
                for paftolGene in readNameGeneDict[readName]:
                    with open(self.makeGeneFname(paftolGene.name, True), 'a') as f:
                        f.write('>%s\n%s\n' % (fwdReadTitle, fwdReadSeq))
        fForward.close()

    def distributePaired(self, result):
        # FIXME: consider try...finally to ensure files are closed
        fForward = open(result.forwardFastq, 'r')
        fqiForward = Bio.SeqIO.QualityIO.FastqGeneralIterator(fForward)
        fReverse = open(result.reverseFastq, 'r')
        fqiReverse = Bio.SeqIO.QualityIO.FastqGeneralIterator(fReverse)
        readNameGeneDict = result.paftolTargetSet.makeReadNameGeneDict()
        for fwdReadTitle, fwdReadSeq, fwdReadQual in fqiForward:
            readName = fwdReadTitle.split()[0]
            # FIXME: premature end of reverse fastq will trigger
            # StopIteration and premature end of forward will leave
            # rest of reverse ignored
            revReadTitle, revReadSeq, revReadQual = fqiReverse.next()
            if readName != revReadTitle.split()[0]:
                raise StandardError('paired read files %s / %s out of sync at read %s / %s' % (result.forwardFastq, result.reverseFastq, fwdReadTitle, revReadTitle))
            if readName in readNameGeneDict:
                for paftolGene in readNameGeneDict[readName]:
                    with open(self.makeGeneFname(paftolGene.name, True), 'a') as f:
                        f.write('>%s\n%s\n' % (fwdReadTitle, fwdReadSeq))
                        f.write('>%s\n%s\n' % (revReadTitle, revReadSeq))
        # FIXME: check for dangling stuff in reverse: should trigger
        # an exception:
        # revReadTitle, revReadSeq, revReadQual = fqiReverse.next()
        fForward.close()
        fReverse.close()

    def distribute(self, result):
        if result.isPaired():
            self.distributePaired(result)
        else:
            self.distributeSingle(result)

    def makeGeneDirname(self, geneName):
        return 'spades-%s' % geneName

    def makeGeneDirPath(self, geneName):
        return os.path.join(self.makeWorkDirname(), self.makeGeneDirname(geneName))

    def assembleGeneSpades(self, result, geneName):
        geneFname = self.makeGeneFname(geneName)
        if not os.path.exists(os.path.join(self.makeWorkDirname(), geneFname)):
            logger.debug('gene fasta file %s does not exist (no reads?)', geneFname)
            return None
        if result.isPaired():
            # FIXME: tight implicit coupling with distributeSingle / distributePaired
            libraryType = paftol.tools.SpadesRunner.INTERLACED
        else:
            libraryType = paftol.tools.SpadesRunner.SINGLE
        spadesOutputDirname = self.makeGeneDirPath(geneName)
        spadesContigList = self.spadesRunner.assemble(geneFname, libraryType, spadesOutputDirname, self.makeWorkDirname())
        return spadesContigList

    def translateGene(self, geneDna):
        # FIXME: add support for gene specific translation table setting
        l = len(geneDna) - (len(geneDna) % 3)
        if l < len(geneDna):
            logger.warning('gene %s: length %d is not an integer multiple of 3 -- not a CDS?', geneDna.id, len(geneDna))
        geneProtein = Bio.SeqRecord.SeqRecord(geneDna.seq[:l].translate(), id='%s-pep' % geneDna.id, description='%s, translated' % geneDna.description)
        return geneProtein

    def filterByPercentIdentity(self, exonerateResultList):
        return [e for e in exonerateResultList if e.percentIdentity >= self.exoneratePercentIdentityThreshold]

    def filterByContainment(self, exonerateResultList):

        def isContainedWithTiebreak(exonerateResult, other):
            if not other.containsQueryAlignmentRange(exonerateResult):
                return False
            if not exonerateResult.containsQueryAlignmentRange(other):
                return True
            # prefer shorter target alignment length (fewer gaps)
            if exonerateResult.targetAlignmentLength < other.targetAlignmentLength:
                return False
            elif exonerateResult.targetAlignmentLength > other.targetAlignmentLength:
                return True
            # subsequent tie breaking is arbitrary and intended to yield consistent results only
            # FIXME: resolving tie by arbitrarily preferring target start position
            if exonerateResult.targetAlignmentStart < other.targetAlignmentStart:
                return False
            elif exonerateResult.targetAlignmentStart > other.targetAlignmentStart:
                return True
            # FIXME: resolving tie using contig id, consider using more meaningful criteria but be mindful of biases...???
            if exonerateResult.targetId is None:
                raise StandardError('cannot break tie when exonerateResult.targetId is None')
            if other.targetId is None:
                raise StandardError('cannot break tie when other.targetId is None')
            if exonerateResult.targetId < other.targetId:
                return False
            elif other.targetId < exonerateResult.targetId:
                return True
            raise StandardError('cannot break tie: exonerateResult = %s, other = %s' % (str(exonerateResult), str(other)))

        nonContainedExonerateResultList = []
        for exonerateResult in exonerateResultList:
            isContained = False
            for other in exonerateResultList:
                isContained = isContained or ((exonerateResult is not other) and isContainedWithTiebreak(exonerateResult, other))
            if not isContained:
                nonContainedExonerateResultList.append(exonerateResult)
        return nonContainedExonerateResultList

    # query:   gattacatgactcga
    # contig1: gattacatga
    # contig2:      ca--actcga
    # trim contig2 because it has (more) gaps in the overlap region??
    # compute consensus -- along overlapping regions, or along entire query?
    def filterByOverlap(self, exonerateResultList):
        logger.warning('scanning for overlaps but not resolving them, pending development of concept')
        nonOverlappingExonerateResultList = []
        for exonerateResult in exonerateResultList:
            for other in exonerateResultList:
                if exonerateResult is not other:
                    if exonerateResult.overlapsQueryAlignmentRange(other):
                        logger.warning('overlap found, but not resolved: %s, %s', str(exonerateResult), str(other))
            nonOverlappingExonerateResultList.append(exonerateResult)
        return nonOverlappingExonerateResultList

    def filterExonerateResultList(self, geneName, exonerateResultList):
        logger.debug('gene %s: %d exonerate results', geneName, len(exonerateResultList))
        exonerateResultList = self.filterByPercentIdentity(exonerateResultList)
        logger.debug('gene %s: %d sufficiently close exonerate results', geneName, len(exonerateResultList))
        exonerateResultList = self.filterByContainment(exonerateResultList)
        logger.debug('gene %s: %d non-contained exonerate results', geneName, len(exonerateResultList))
        exonerateResultList = self.filterByOverlap(exonerateResultList)
        logger.debug('gene %s: %d non-overlapping exonerate results', geneName, len(exonerateResultList))
        return exonerateResultList

    def reconstructCds(self, result, geneName):
        logger.debug('reconstructing CDS for gene %s', geneName)
        if result.representativePaftolTargetDict is None:
            raise StandardError('illegal state: no represesentative genes')
        if result.representativePaftolTargetDict[geneName] is None:
            raise StandardError('no representative for gene %s' % geneName)
        os.mkdir(self.makeGeneDirPath(geneName))
        contigList = self.assembleGeneSpades(result, geneName)
        if contigList is None:
            logger.warning('gene %s: no spades contigs', geneName)
            return None
        if len(contigList) == 0:
            logger.warning('gene %s: empty contig list', geneName)
            return None
        logger.debug('gene %s: %d spades contigs', geneName, len(contigList))
        geneProtein = self.translateGene(result.representativePaftolTargetDict[geneName].seqRecord)
        aminoAcidSet = set(Bio.Alphabet.IUPAC.protein.letters.lower())
        # allow stop translation
        aminoAcidSet.add('*')
        setDiff = set(str(geneProtein.seq).lower()) - aminoAcidSet
        if len(setDiff) > 0:
            logger.warning('gene %s: invalid amino acids %s' % (geneName, ', '.join(setDiff)))
            return None
        contigFname = os.path.join(self.makeGeneDirPath(geneName), '%s-contigs.fasta' % geneName)
        Bio.SeqIO.write(contigList, contigFname, 'fasta')
        exonerateRunner = paftol.tools.ExonerateRunner()
        exonerateResultList = exonerateRunner.parse(geneProtein, contigFname, 'protein2genome', len(contigList))
        logger.debug('%d contigs, %d exonerate results', len(contigList), len(exonerateResultList))
        if len(exonerateResultList) == 0:
            logger.warning('gene %s: no exonerate results from %d contigs', geneName, len(contigList))
        exonerateResultList.sort(cmpExonerateResultByQueryAlignmentStart)
        for exonerateResult in exonerateResultList:
            if exonerateResult.targetStrand == '-':
                exonerateResult.reverseComplementTarget()
        logger.warning('provisional filtering and supercontig construction, handling of overlapping contigs not finalised')
        filteredExonerateResultList = self.filterExonerateResultList(geneName, exonerateResultList)
        if len(filteredExonerateResultList) == 0:
            logger.warning('gene %s: no exonerate results left after filtering', geneName)
            return None
        supercontig = Bio.SeqRecord.SeqRecord(Bio.Seq.Seq(''.join([str(e.targetCdsSeq.seq) for e in filteredExonerateResultList])), id='%s_supercontig' % geneName)
        if len(supercontig) == 0:
            logger.warning('gene %s: empty supercontig', geneName)
            return None
        supercontigFname = os.path.join(self.makeGeneDirPath(geneName), '%s-supercontig.fasta' % geneName)
        Bio.SeqIO.write([supercontig], supercontigFname, 'fasta')
        supercontigErList = exonerateRunner.parse(geneProtein, supercontigFname, 'protein2genome', len(contigList))
        if len(supercontigErList) == 0:
            logger.warning('gene %s: no exonerate results from supercontig', geneName)
            return None
        # not filtering for percent identity to gene again, as that is already done
        if result.reverseFastq is not None:
            readsSpec = '%s, %s' % (result.forwardFastq, result.reverseFastq)
        else:
            readsSpec = result.forwardFastq
        splicedSupercontig = Bio.SeqRecord.SeqRecord(Bio.Seq.Seq(''.join([str(e.targetCdsSeq.seq) for e in supercontigErList])), id=geneName, description='reconstructed CDS computed by paftol.HybpiperAnalyser, targets: %s, reads: %s' % (result.paftolTargetSet.fastaHandleStr, readsSpec))
        return splicedSupercontig


class HybpiperBwaAnalyser(HybpiperAnalyser):

    """L{HybseqAnalyser} subclass that implements an analysis process
close to the HybPiper pipeline.

Some parameters to SPAdes can be controlled via instance variables as
documented below. Defaults of these parameters correspond to the
defaults provided by SPAdes, respectively (at the time of developing
this).

@ivar bwaRunner: SPAdes runner, providing instance variables for configuring BWA
@type bwaRunner: C{paftol.tools.BwaRunner}
@ivar spadesRunner: SPAdes runner, providing instance variables for configuring SPAdes
@type spadesRunner: C{paftol.tools.SpadesRunner}

    """

    def __init__(self, workdirTgz=None, workDirname='pafpipertmp', bwaRunner=None, spadesRunner=None):
        super(HybpiperBwaAnalyser, self).__init__(workdirTgz, workDirname)
        if bwaRunner is None:
            self.bwaRunner = paftol.tools.BwaRunner()
        else:
            self.bwaRunner = bwaRunner
        if spadesRunner is None:
            self.spadesRunner = paftol.tools.SpadesRunner()
        else:
            self.spadesRunner = spadesRunner
        # FIXME: obsolete, should get summary stats from result now
        self.statsCsvFilename = None
        self.exoneratePercentIdentityThreshold = 65.0

    def setup(self, result):
        logger.debug('setting up')
        self.setupTmpdir()
	result.paftolTargetSet.writeFasta(self.makeTargetsFname(True))

    def mapReadsBwa(self, result):
        """Map reads to gene sequences (from multiple organisms possibly).
"""
        logger.debug('mapping reads to gene sequences')
        referenceFname = self.makeTargetsFname(True)
        self.bwaRunner.indexReference(referenceFname)
        forwardReadsFname = os.path.join(os.getcwd(), result.forwardFastq)
        if result.reverseFastq is None:
            reverseReadsFname = None
        else:
            reverseReadsFname = os.path.join(os.getcwd(), result.reverseFastq)
        self.bwaRunner.processBwa(result.paftolTargetSet, referenceFname, forwardReadsFname, reverseReadsFname)

    # ideas for hybrid / consensus sequence for (multiple) re-mapping
    # reference CDS:     atgtac------catacagaagagacgtga
    # reconstructed CDS:    cactcatttcat---gga
    # "consensus"        atgCACTCAATTCAT   GGAgagacgtga
    # principe: Where reconstructed symbol is available, use that in preference.
    #   * gap in reference: use symbols from reconstructed (must be non-gap if pairwise alignment)
    #   * gap in reconstructed: skip symbols from reference
    #   * ends / portions with no alignment to reconstructed: fill in from reference
    # Problem: avoid non-homologous alignment portions (e.g. around borders of reconstructed)?

    def analyse(self, targetsSourcePath, forwardFastq, reverseFastq, allowInvalidBases):
        logger.debug('starting')
	paftolTargetSet = PaftolTargetSet()
	paftolTargetSet.readFasta(targetsSourcePath)
        # FIXME: put allowInvalidBases in result for subsequent reference?
	paftolTargetSet.sanityCheck(allowInvalidBases)
        result = HybpiperResult(paftolTargetSet, forwardFastq, reverseFastq)
	try:
            self.setup(result)
            logger.debug('setup done')
            self.mapReadsBwa(result)
            logger.debug('BWA mapping done')
            self.distribute(result)
            logger.debug('read distribution done')
            self.setRepresentativeGenes(result)
            logger.debug('representative genes selected')
            result.reconstructedCdsDict = {}
            for geneName in result.paftolTargetSet.paftolGeneDict:
                result.reconstructedCdsDict[geneName] = self.reconstructCds(result, geneName)
	    logger.debug('CDS reconstruction done')
	    # FIXME: self.statsCsvFilename no longer required, caller can get that from result now
            if self.statsCsvFilename is not None:
                tStats = result.paftolTargetSet.targetStats()
                with open(self.statsCsvFilename, 'w') as csvFile:
                    tStats.writeCsv(csvFile)
                csvFile.close()
                logger.debug('CSV file written')
            logger.debug('finished')
            return result 
        finally:
            self.makeTgz()
            logger.debug('tgz file made')
            self.cleanup()
            logger.debug('cleanup done')

            
class HybseqResult(object):
    
    def __init__(self):
        self.reconstructedCdsDict = None 

    def summaryStats(self):
        raise StandardError, 'not implemented by this abstract class'
        

class HybpiperResult(HybseqResult):
    
    def __init__(self, paftolTargetSet, forwardFastq, reverseFastq):
        super(HybpiperResult, self).__init__()
        self.paftolTargetSet = paftolTargetSet
        self.forwardFastq = forwardFastq
        self.reverseFastq = reverseFastq
        self.representativePaftolTargetDict = None
    
    def isPaired(self):
        return self.reverseFastq is not None

    def summaryStats(self):
        if self.reconstructedCdsDict is None:
	    raise StandardError, 'Illegal state, reconstructedCdsDict not populated'
	summaryColumnList = ['sampleName', 'targetsFile', 'paftolGene', 'paftolOrganism', 'paftolTargetLength', 'numReadsFwd', 'numReadsRev', 'qual28Fwd', 'qual28Rev', 'meanA', 'stddevA', 'meanC', 'stddevC', 'meanG', 'stddevG', 'meanT', 'stddevT', 'meanN', 'stddevN', 'numMappedReads', 'totNumUnmappedReads', 'hybpiperCdsLength', 'representativeTarget']
        summaryDataFrame = paftol.tools.DataFrame(summaryColumnList)
        rowDict = {}
        for columnName in summaryColumnList:
            rowDict[columnName] = None
        rowDict['numReadsFwd'] = paftol.tools.countSeqRecords(self.forwardFastq, 'fastq')
        rowDict['numReadsRev'] = paftol.tools.countSeqRecords(self.reverseFastq, 'fastq')
        paftolSampleId = extractPaftolSampleId(self.forwardFastq)
        rowDict['sampleName'] = paftolSampleId
        rowDict['targetsFile'] = self.paftolTargetSet.fastaHandleStr
        fqDataFrameFwd = generateFastqcDataFrame(self.forwardFastq)
        fqDataFrameRev = generateFastqcDataFrame(self.reverseFastq)
        rowDict['qual28Fwd'] = getQual28(fqDataFrameFwd.perBaseSequenceQuality)
        rowDict['qual28Rev'] = getQual28(fqDataFrameRev.perBaseSequenceQuality)
        perBaseSequenceContentFwd = fqDataFrameFwd.calculateMeanStd(fqDataFrameFwd.perBaseSequenceContent)
        perBaseSequenceContentRev = fqDataFrameRev.calculateMeanStd(fqDataFrameRev.perBaseSequenceContent)
        rowDict['meanA'] = (perBaseSequenceContentFwd['a'].mean + perBaseSequenceContentRev['a'].mean) / 2.0 
        rowDict['stddevA'] = (perBaseSequenceContentFwd['a'].std + perBaseSequenceContentRev['a'].std) / 2.0
        rowDict['meanC'] = (perBaseSequenceContentFwd['c'].mean + perBaseSequenceContentRev['c'].mean) / 2.0
        rowDict['stddevC'] = (perBaseSequenceContentFwd['c'].std + perBaseSequenceContentRev['c'].std) / 2.0
        rowDict['meanG'] = (perBaseSequenceContentFwd['g'].mean + perBaseSequenceContentRev['g'].mean) / 2.0
        rowDict['stddevG'] = (perBaseSequenceContentFwd['g'].std + perBaseSequenceContentRev['g'].std) / 2.0
        rowDict['meanT'] = (perBaseSequenceContentFwd['t'].mean + perBaseSequenceContentRev['t'].mean) / 2.0
        rowDict['stddevT'] = (perBaseSequenceContentFwd['t'].std + perBaseSequenceContentRev['t'].std) / 2.0
        perBaseNContentFwd = fqDataFrameFwd.calculateMeanStd(fqDataFrameFwd.perBaseNContent)
        perBaseNContentRev = fqDataFrameRev.calculateMeanStd(fqDataFrameRev.perBaseNContent)
        rowDict['meanN'] = (perBaseNContentFwd['nCount'].mean + perBaseNContentRev['nCount'].mean) / 2.0
        rowDict['stddevN'] = (perBaseNContentFwd['nCount'].std + perBaseNContentRev['nCount'].std) / 2.0
        rowDict['totNumUnmappedReads'] = self.paftolTargetSet.numOfftargetReads 
        targetSeqRecordList = self.paftolTargetSet.getSeqRecordList()
        for paftolOrganism in self.paftolTargetSet.organismDict.values():
            rowDict['paftolOrganism'] = paftolOrganism.name
            for paftolTarget in paftolOrganism.paftolTargetDict.values():
                geneName = paftolTarget.paftolGene.name
                rowDict['paftolGene'] = paftolTarget.paftolGene.name
                rowDict['paftolTargetLength'] = len(paftolTarget.seqRecord)
                rowDict['numMappedReads'] = paftolTarget.numMappedReads()
                hybpiperCdsLength = None
                if paftolTarget.paftolGene.name in self.reconstructedCdsDict and self.reconstructedCdsDict[paftolTarget.paftolGene.name] is not None:
                    hybpiperCdsLength = len(self.reconstructedCdsDict[paftolTarget.paftolGene.name])
                rowDict['hybpiperCdsLength'] = hybpiperCdsLength
                representativeTarget = None
                if geneName in self.representativePaftolTargetDict:
                    representativeTarget = self.representativePaftolTargetDict[geneName]
                rowDict['representativeTarget'] = representativeTarget.seqRecord.id
                summaryDataFrame.addRow(rowDict)
        return summaryDataFrame


# FIXME: also extracts more generic sample IDs now, so name is not quite right
def extractPaftolSampleId(fastqName):
    paftolFastqRe = re.compile('([A-Z0-9_-]+)_L001_R([12])')
    illuminaPairedFastqRe = re.compile('([^.])_R1[^.]*\\.fastq')
    genericFastqRe = re.compile('([^.]+)\\.fastq')
    for r in [paftolFastqRe, illuminaPairedFastqRe, genericFastqRe]:
        m = r.match(fastqName)
        if m is not None:
            return m.group(1)
    raise StandardError, 'invalid PAFTOL fastq sample file name: %s' % fastqName


def getQual28(fastqcDataFrame):
    medianList = fastqcDataFrame.getColumn('median')
    baseList = fastqcDataFrame.getColumn('base')
    index = 0
    boolVar = 0
    while (index < len(medianList)) and (boolVar == 0):
        if (medianList[index] < 28):
            pos = index
            boolVar = 1
        index = index + 1
    if boolVar == 0:
        return 0
    else:
        return baseList[pos]

def paftolSummary(paftolTargetFname, fastqPairList, bwaRunner):
    summaryColumnList = ['sampleName', 'targetsFile', 'paftolGene', 'paftolOrganism', 'paftolTargetLength', 'numReadsFwd', 'numReadsRev', 'qual28Fwd', 'qual28Rev', 'meanA', 'stddevA', 'meanC', 'stddevC', 'meanG', 'stddevG', 'meanT', 'stddevT', 'meanN', 'stddevN', 'numMappedReads', 'totNumUnmappedReads', 'hybpiperCdsLength']
    summaryDataFrame = paftol.tools.DataFrame(summaryColumnList)
    for fastqFwd, fastqRev in fastqPairList:
        logger.debug('fastqPair: %s, %s' % (fastqFwd, fastqRev))
        rowDict = {}
        for columnName in summaryColumnList:
            rowDict[columnName] = None
        rowDict['numReadsFwd'] = paftol.tools.countSeqRecords(fastqFwd, 'fastq')
        rowDict['numReadsRev'] = paftol.tools.countSeqRecords(fastqRev, 'fastq')
        paftolSampleId = extractPaftolSampleId(fastqFwd)
        rowDict['sampleName'] = paftolSampleId
        rowDict['targetsFile'] = paftolTargetFname 
        fqDataFrameFwd = generateFastqcDataFrame(fastqFwd)
        fqDataFrameRev = generateFastqcDataFrame(fastqRev)
        rowDict['qual28Fwd'] = getQual28(fqDataFrameFwd.perBaseSequenceQuality)
        rowDict['qual28Rev'] = getQual28(fqDataFrameRev.perBaseSequenceQuality)
        perBaseSequenceContentFwd = fqDataFrameFwd.calculateMeanStd(fqDataFrameFwd.perBaseSequenceContent)
        perBaseSequenceContentRev = fqDataFrameRev.calculateMeanStd(fqDataFrameRev.perBaseSequenceContent)
        rowDict['meanA'] = (perBaseSequenceContentFwd['a'].mean + perBaseSequenceContentRev['a'].mean) / 2.0 
        rowDict['stddevA'] = (perBaseSequenceContentFwd['a'].std + perBaseSequenceContentRev['a'].std) / 2.0
        rowDict['meanC'] = (perBaseSequenceContentFwd['c'].mean + perBaseSequenceContentRev['c'].mean) / 2.0
        rowDict['stddevC'] = (perBaseSequenceContentFwd['c'].std + perBaseSequenceContentRev['c'].std) / 2.0
        rowDict['meanG'] = (perBaseSequenceContentFwd['g'].mean + perBaseSequenceContentRev['g'].mean) / 2.0
        rowDict['stddevG'] = (perBaseSequenceContentFwd['g'].std + perBaseSequenceContentRev['g'].std) / 2.0
        rowDict['meanT'] = (perBaseSequenceContentFwd['t'].mean + perBaseSequenceContentRev['t'].mean) / 2.0
        rowDict['stddevT'] = (perBaseSequenceContentFwd['t'].std + perBaseSequenceContentRev['t'].std) / 2.0
        perBaseNContentFwd = fqDataFrameFwd.calculateMeanStd(fqDataFrameFwd.perBaseNContent)
        perBaseNContentRev = fqDataFrameRev.calculateMeanStd(fqDataFrameRev.perBaseNContent)
        rowDict['meanN'] = (perBaseNContentFwd['nCount'].mean + perBaseNContentRev['nCount'].mean) / 2.0
        rowDict['stddevN'] = (perBaseNContentFwd['nCount'].std + perBaseNContentRev['nCount'].std) / 2.0
        hybpiperAnalyser = HybpiperAnalyser(paftolTargetFname, fastqFwd, fastqRev, bwaRunner=bwaRunner)
        hybpiperResult = hybpiperAnalyser.analyse()
        rowDict['totNumUnmappedReads'] = hybpiperAnalyser.paftolTargetSet.numOfftargetReads 
        targetSeqRecordList = hybpiperAnalyser.paftolTargetSet.getSeqRecordList()
        for paftolOrganism in hybpiperAnalyser.paftolTargetSet.organismDict.values():
            rowDict['paftolOrganism'] = paftolOrganism.name
            for paftolTarget in paftolOrganism.paftolTargetDict.values():
                rowDict['paftolGene'] = paftolTarget.paftolGene.name
                rowDict['paftolTargetLength'] = len(paftolTarget.seqRecord)
                rowDict['numMappedReads'] = paftolTarget.numMappedReads()
                hybpiperCdsLength = None
                if paftolTarget.paftolGene.name in hybpiperResult.reconstructedCdsDict and hybpiperResult.reconstructedCdsDict[paftolTarget.paftolGene.name] is not None:
                    hybpiperCdsLength = len(hybpiperResult.reconstructedCdsDict[paftolTarget.paftolGene.name])
                rowDict['hybpiperCdsLength'] = hybpiperCdsLength
                summaryDataFrame.addRow(rowDict)
        tmpCsvFname = 'tmp_%s.csv' % paftolSampleId
        with open(tmpCsvFname, 'w') as tmpCsv:
            summaryDataFrame.writeCsv(tmpCsv)
    return summaryDataFrame
        
